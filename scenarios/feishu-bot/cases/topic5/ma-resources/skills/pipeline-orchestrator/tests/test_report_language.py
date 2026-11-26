"""Regression: source opinions cannot make report generation retry forever."""
import importlib.util
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import charts
import handlers
import report_contract
import report_language
import validator
import wordcloud_adapter
from _util import make_state


class TestReportLanguage(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.state = make_state(self.work.name)
        self.label = "联名活动性价比认可"
        self.quote = "套餐性价比不错，用户也讨论人均消费和购买转化。"
        rows = [{
            "id": str(index), "content_mode": "UGC", "content": self.quote,
            "title": "用户性价比评价", "url": f"https://example.com/{index}",
            "site_name": "小红书", "_platform": "xiaohongshu",
            "_publish_ymd": "20261001", "interaction_cnt": index,
            "ai_sentiment": 1, "opinion": self.label,
            "ugc_category": "套餐实测", "discussion_dimension": "产品",
        } for index in range(12)]
        bundle = handlers._campaign_stats(rows, ["套餐"], [], True)
        self.stats = {
            "brand": {"trend": {"xiaohongshu": {"20261001": 12}},
                      "wordcloud": {"xiaohongshu": {"套餐": 12}},
                      "top_content": report_contract.top_content(rows)},
            "campaigns": {"联名活动": bundle},
            "clean_record": {"total_in": 12, "total_out": 12},
        }
        self.plan = {
            "platforms": ["xiaohongshu"], "brand_query": "品牌", "activity_query": "联名",
            "activity_filter": "广告", "period_start": "2026-10-01", "period_end": "2026-10-01",
        }
        self.insight = {"brand": {"overall": {"platforms": {"xiaohongshu": {
            "topics": [{"topic": "套餐讨论", "words": ["套餐"], "insight": "样本讨论套餐。"}],
        }}}}}
        self.charts = {"charts": []}
        for spec in charts.build_chart_specs(self.stats, {}):
            path = Path(self.work.name) / f"{spec['chart_id']}.png"
            path.write_bytes(b"image")
            self.charts["charts"].append({**spec, "spec": spec, "local_path": str(path)})
        # Start with a clean model draft; only assembly adds the source wording.
        self.draft = "# 联名复盘\n\n## 00 篇头 · 数据说明\n分析周期为2026年10月1日。\n"
        self.draft += "\n## 整体洞察\n活动样本12条，用户讨论集中在套餐。\n## 01 品牌传播表现\n"
        for index, title in enumerate(("传播概况", "口碑分析", "内容分析", "热门内容"), 1):
            self.draft += f"\n### 1.{index} {title}\n本周期样本12条。\n"
        self.draft += "\n## 02 活动传播表现\n### 2.1 活动 联名活动\n"
        for index, title in enumerate(
            ("传播概况", "活动声量渗透分析", "口碑分析", "内容分析", "热门内容"), 1
        ):
            self.draft += f"\n#### 2.1.{index} {title}\n活动样本12条。\n"
        self.draft += (
            "\n## 03 综合表现定位\n### 3.1 基准期对比\n基准不可用。\n"
            "## 04 经验总结\n### 4.1 成功经验\n2.1.3中正面样本12条，套餐体验获得讨论。\n"
            "### 4.2 待改进问题\n2.1.3仅12条样本，尚不能代表全体用户。\n"
            "### 4.3 行动计划\n围绕2.1.3的12条样本继续收集负面反馈。\n"
            "## 05 总结与建议\n后续跟踪套餐反馈与价格讨论。\n"
        )
        for key, value in (
            ("stats_bundle", self.stats), ("confirmed_plan", self.plan),
            ("requirement_confirmed", self.plan), ("chart_urls", self.charts),
            ("wordcloud_insight", self.insight),
        ):
            self.state.write_artifact(key, value)

    def write_report(self):
        with patch("semantic.llm_available", return_value=True), \
                patch("semantic.chat", return_value=self.draft) as model:
            report = handlers.write_report({"id": "n16"}, self.state, {})["artifacts"]["report_md"]
        self.assertEqual(model.call_count, 1)
        return report

    def test_n16_and_full_validator_accept_source_price_opinion_and_quotes(self):
        report = self.write_report()
        self.assertIn(self.label, report["markdown"])
        self.assertIn(self.quote, report["markdown"])
        self.assertEqual(report["guardrail_removed_sentences"], [])
        qc = validator.validate_report(
            report, requirement=self.plan, stats=self.stats, charts=self.charts)
        self.assertEqual(qc["p0_count"], 0, qc["issues"])
        self.assertEqual(qc["semantic_review"], [])
        self.assertEqual(qc["rules_version"], "topic5-validator-qc-v1.9")

    def test_n19_does_not_reintroduce_the_same_assembly_failure(self):
        report = self.write_report()
        self.state.write_artifact("report_md", report)
        self.state.write_artifact("qc_report", {
            "issues": [{"id": "P0-test", "severity": "P0", "title": "补充说明"}]})
        with patch("semantic.llm_available", return_value=True), \
                patch("semantic.chat", return_value=report["markdown"]) as model:
            repaired = handlers.fix_report({"id": "n19"}, self.state, {})["artifacts"]["report_md"]
        self.assertEqual(model.call_count, 1)
        self.assertIn(self.quote, repaired["markdown"])
        self.assertIn(self.label, repaired["markdown"])
        self.assertEqual(repaired["deterministic_preflight"]["p0_count"], 0)

    def test_quote_exemption_requires_matching_source_and_link(self):
        report = self.write_report()["markdown"]
        forged = report.replace(self.quote, "用户认为购买转化率已经达到80%。")
        findings = report_language.metric_violations(forged, stats=self.stats)
        self.assertTrue(any(term == "转化率" for _, term, _ in findings))
        wrong_link = report.replace(
            f"> 「{self.quote}」 [原文](https://example.com/11)",
            f"> 「{self.quote}」 [原文](https://example.com/unknown)")
        self.assertTrue(report_language.metric_violations(wrong_link, stats=self.stats))
        self.assertTrue(report_language.metric_violations(report, stats={}))

    def test_source_label_is_exempt_only_in_its_evidence_module(self):
        label = "人均套餐价格认可"
        bundle = {"opinion_analysis": {"positive": {
            "sample_size": 12, "top5": [{"label": label, "count": 12, "share": 1}],
        }}}
        stats = {"campaigns": {"联名活动": bundle}}
        evidence = "#### 2.1.3 口碑分析\n" + report_contract.opinion_text(bundle)
        self.assertEqual(report_language.metric_violations(evidence, stats=stats), [])
        prose = evidence + "\n## 05 总结与建议\n**人均套餐价格认可**：本次活动结论。\n"
        self.assertTrue(report_language.metric_violations(prose, stats=stats))

    def test_quote_does_not_exempt_an_appended_unsupported_metric_claim(self):
        report = self.write_report()["markdown"] + "\n本次活动转化率高达80%。"
        with self.assertRaisesRegex(ValueError, r"转化率.*L\d+"):
            handlers._assert_no_forbidden_terms(report, stats=self.stats)
        qc = validator.validate_report({"markdown": report}, stats=self.stats, charts=self.charts)
        issue = next(issue for issue in qc["issues"] if issue["id"] == "P0-forbidden-转化率")
        self.assertIn("本次活动转化率高达80%", issue["evidence"])

    def test_price_conclusion_requires_semantic_evidence_review(self):
        report = self.write_report()["markdown"] + "\n本次合作性价比最高，应扩大投入。"
        qc = validator.validate_report({"markdown": report}, stats=self.stats, charts=self.charts)
        self.assertFalse(any(issue["id"] == "P0-forbidden-性价比" for issue in qc["issues"]))
        self.assertEqual(len(qc["semantic_review"]), 1)
        self.assertIn("本次合作性价比最高", qc["semantic_review"][0]["evidence"])

    def test_nested_source_quotes_are_not_treated_as_analysis_metrics(self):
        row = {"content": "用户说「人均消费不低」，希望套餐优惠。"}
        bundle = {"opinion_analysis": {"positive": {
            "top5": [{"label": "套餐价格讨论", "count": 12, "share": 1, "representatives": [row]}],
            "sample_size": 12,
        }}}
        stats = {"campaigns": {"联名活动": bundle}}
        md = "#### 2.1.3 口碑分析\n" + report_contract.opinion_text(bundle)
        self.assertEqual(report_language.metric_violations(md, stats=stats), [])

    def test_wordcloud_can_describe_price_discussion(self):
        topic = {"title": "用户对套餐性价比的评价", "terms": ["性价比"],
                 "interpretation": "原词表中出现性价比，反映用户价格讨论，不代表合作投入产出。",
                 "rationale": "依据原词表", "post_ids": []}
        wordcloud_adapter.validate_topics(
            {"topics": [topic]}, {"性价比"}, set(), handlers.FORBIDDEN_METRIC_TERMS)

    def test_packaged_scanner_loads_the_same_policy_without_source_tree(self):
        scanner = Path(validator._skill_scan_path())
        policy = Path(report_language.__file__)
        report = self.write_report()
        for layout in ("pipeline-orchestrator", "topic5-validator/scripts"):
            with self.subTest(layout=layout), tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / layout
                target.mkdir(parents=True)
                script = target / ("validator_scan.py" if layout == "pipeline-orchestrator" else "scan_report.py")
                shutil.copyfile(scanner, script)
                shutil.copyfile(policy, target / "report_language.py")
                spec = importlib.util.spec_from_file_location("isolated_validator", script)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                qc = module.scan_markdown(
                    report["markdown"], requirement=self.plan, stats=self.stats, charts=self.charts)
                self.assertEqual(qc["p0_count"], 0, qc["issues"])
