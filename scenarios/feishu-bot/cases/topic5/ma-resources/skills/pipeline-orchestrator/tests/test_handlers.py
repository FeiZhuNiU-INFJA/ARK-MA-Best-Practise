"""Unit tests for handlers.py: registry, HumanPause, schema coverage.

Covers every non-human/non-decision node's handler (returns `artifacts`
keyed to the node's outputs), decision routes in the node's route set, and
human handlers raising HumanPause.
"""
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import handlers
import hc_bridge
from handlers import HumanPause
from _util import compliant_pipeline_services, load_dag, make_state


class TestRegistry(unittest.TestCase):
    def test_get_known(self):
        self.assertIs(handlers.get("intake_check"), handlers.intake_check)

    def test_get_unknown_raises_keyerror(self):
        with self.assertRaises(KeyError):
            handlers.get("does_not_exist")


class TestHumanGate(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp()
        self.st = make_state(self.ws)

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    def test_human_gate_raises_with_prompt_and_expect(self):
        node = {"id": "n01", "name": "需求确认", "outputs": ["requirement_confirmed"]}
        with self.assertRaises(HumanPause) as cm:
            handlers.human_gate(node, self.st, {})
        self.assertEqual(cm.exception.prompt, "需求确认")
        self.assertEqual(cm.exception.expect, ["requirement_confirmed"])


class TestSpecificHandlers(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp()
        self.st = make_state(self.ws)

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    def test_ugc_discovery_fixed_four(self):
        self.st.params["ugc_category_mode"] = "fixed_four"
        node = {"id": "n08", "name": "ugc"}
        out = handlers.ugc_discovery(node, self.st, {})
        cats = out["artifacts"]["ugc_category_list"]["categories"]
        self.assertEqual(len(cats), 4)
        self.assertEqual(out["artifacts"]["ugc_category_list"]["mode"], "fixed_four")

    def test_ugc_discovery_dynamic(self):
        self.st.params["ugc_category_mode"] = "dynamic"
        with self.assertRaisesRegex(RuntimeError, "能力不可用"):
            handlers.ugc_discovery({"id": "n08", "name": "ugc"}, self.st, {})

    def test_opinion_candidates_returns_artifact(self):
        with self.assertRaisesRegex(RuntimeError, "能力不可用"):
            handlers.opinion_candidates({"id": "n09b", "name": "oc"}, self.st, {})

    def test_opinion_policy_gate_auto_confirms_stable_labels(self):
        self.st.write_artifact("opinion_candidates", {
            "candidates": ["口味体验", "包装设计", "价格感知", "购买便利", "品牌联想"],
            "source": "llm",
            "evidence_count": 12,
        })
        self.st.write_artifact("ugc_category_list", {
            "mode": "fixed_four",
            "categories": ["模仿打卡", "创意改编", "测评分享", "自发安利"],
        })
        out = handlers.opinion_policy_gate(
            {"id": "n10_hc_opinion", "outputs": ["confirmed_opinion_labels"]},
            self.st,
            {},
        )
        confirmed = out["artifacts"]["confirmed_opinion_labels"]
        self.assertTrue(confirmed["automatic"])
        self.assertEqual(confirmed["confirmed_by"], "validator-policy")

    def test_opinion_policy_gate_pauses_on_ambiguous_labels(self):
        self.st.write_artifact("opinion_candidates", {
            "candidates": ["正面", "正面", "其他"],
            "source": "llm",
            "evidence_count": 1,
        })
        self.st.write_artifact("ugc_category_list", {
            "mode": "dynamic",
            "categories": ["其他"],
        })
        with self.assertRaises(HumanPause):
            handlers.opinion_policy_gate(
                {"id": "n10_hc_opinion", "outputs": ["confirmed_opinion_labels"]},
                self.st,
                {},
            )

    def test_run_init_sets_resource_lock(self):
        self.st.write_artifact("requirement_confirmed", {
            "confirmed": True,
            "brand": "测试品牌",
            "activity_name": "测试活动",
            "period_start": "2026-01-01",
            "period_end": "2026-01-07",
            "platforms": ["xiaohongshu"],
            "brand_query": "测试品牌",
            "activity_query": "测试活动",
            "content_scope": "all",
            "business_questions": ["活动表现与改进"],
            "delivery_expectation": "飞书营销活动复盘报告",
            "allowed_actions": "调用已登记能力",
            "source_file": {"name": "test.xlsx", "mount_path": "test.xlsx"},
        })
        plan_out = handlers.plan_compile(
            {"id": "n02", "name": "plan"}, self.st, {}
        )
        for key, payload in plan_out["artifacts"].items():
            self.st.write_artifact(key, payload)
        self.st.human = {"node": "n03_hc_plan", "expect": ["confirmed_plan"]}
        confirmed = hc_bridge.build_human_outputs(
            self.st, "pass", ""
        )["confirmed_plan"]
        self.st.write_artifact("confirmed_plan", confirmed)
        source_path = Path(self.ws) / "test.xlsx"
        source_path.write_bytes(b"xlsx-fixture")
        resources = {
            key: "test"
            for key in (
                "dag-contract", "rules-implementation", "coordinator-agent",
                "validator-agent", "environment", "pipeline-orchestrator",
                "topic5-validator", "SSJH-Social-Stat", "blueai-datahub-skill",
                "blueai-yuqing-echarts-skill-new", "report-cover-skill",
                "feishu-delivery-tool", "semantic-tool", "annotation-tool", "model",
            )
        }
        with patch("excel_source.resolve_activity_xlsx", return_value=source_path), \
                patch("excel_source.load_activity_rows", return_value={
                    "rows": [],
                    "meta": {},
                }):
            out = handlers.run_init(
                {"id": "n04", "name": "init",
                 "outputs": ["run_record", "resource_lock", "context_snapshot"]},
                self.st,
                {"resource_versions": resources},
            )
        self.assertIn("resource_lock", self.st.params)
        self.assertRegex(self.st.params["resource_lock"], r"^lock_[0-9a-f]{16}$")
        self.assertIn("run_record", out["artifacts"])

    def test_plan_compile_builds_reviewable_plan_from_confirmed_requirement(self):
        self.st.params["nsr_denominator_includes_neutral"] = True
        self.st.write_artifact("requirement_confirmed", {
            "confirmed": True,
            "brand": "瑞幸",
            "activity_name": "瑞幸联名活动",
            "period_start": "2026-09-28",
            "period_end": "2026-10-07",
            "platforms": ["xiaohongshu"],
            "brand_query": "测试品牌",
            "activity_query": "测试活动",
            "content_scope": "all",
            "product_query": "瑞幸+流心芝士拿铁",
            "business_questions": ["活动表现与改进"],
            "delivery_expectation": "飞书营销活动复盘报告",
            "allowed_actions": "调用已登记能力",
            "campaigns": [{"id": "活动 A"}, {"id": "活动 B"}],
            "source_file": {"name": "活动明细.xlsx", "mount_path": "internal.xlsx"},
        })
        out = handlers.plan_compile({"id": "n02", "name": "方案编制"}, self.st, {})
        analysis = out["artifacts"]["analysis_plan"]
        execution = out["artifacts"]["execution_plan"]
        modules = out["artifacts"]["selected_modules"]

        self.assertNotIn("_stub", analysis)
        self.assertEqual(analysis["scope"]["source_file_name"], "活动明细.xlsx")
        self.assertNotIn("mount_path", analysis["scope"])
        self.assertEqual(
            [metric["name"] for metric in analysis["metrics"]],
            list(handlers.METRIC_WHITELIST),
        )
        nsr = next(metric for metric in analysis["metrics"] if metric["name"] == "NSR")
        self.assertTrue(nsr["neutral_in_denominator"])
        self.assertIn("竞品分析已下线，不生成竞品章节", analysis["guardrails"])
        self.assertEqual(len(execution["stages"]), 5)
        self.assertIn("product_funnel", modules["conditional"])
        self.assertIn("multi_activity_compare", modules["conditional"])

    def test_plan_compile_maps_natural_activity_performance_question(self):
        self.st.write_artifact("requirement_confirmed", {
            "confirmed": True,
            "brand": "瑞幸",
            "activity_name": "瑞幸联名活动",
            "period_start": "2026-09-28",
            "period_end": "2026-10-07",
            "platforms": ["xiaohongshu"],
            "brand_query": "瑞幸",
            "activity_query": "瑞幸联名活动",
            "content_scope": "all",
            "business_questions": [
                "活动实际表现如何",
                "哪些内容和参与方式有效",
                "下次如何改进",
            ],
            "delivery_expectation": "飞书营销活动复盘报告",
            "allowed_actions": "调用已登记能力",
            "source_file": {"name": "activity.xlsx", "mount_path": "activity.xlsx"},
        })
        out = handlers.plan_compile({"id": "n02", "name": "plan"}, self.st, {})
        analysis = out["artifacts"]["analysis_plan"]
        execution = out["artifacts"]["execution_plan"]
        mapping = analysis["objective_capability_map"]
        self.assertTrue(all(item["supported"] for item in mapping))
        self.assertEqual(execution["preflight"]["status"], "READY")

    def test_plan_compile_blocks_unregistered_roi_objective(self):
        self.st.write_artifact("requirement_confirmed", {
            "confirmed": True,
            "brand": "测试品牌",
            "activity_name": "测试活动",
            "period_start": "2026-01-01",
            "period_end": "2026-01-07",
            "platforms": ["xiaohongshu"],
            "brand_query": "测试品牌",
            "activity_query": "测试活动",
            "content_scope": "all",
            "business_questions": ["计算销售转化率与营销 ROI"],
            "delivery_expectation": "飞书营销活动复盘报告",
            "allowed_actions": "调用已登记能力",
            "source_file": {"name": "test.xlsx", "mount_path": "test.xlsx"},
        })
        out = handlers.plan_compile({"id": "n02", "name": "plan"}, self.st, {})
        preflight = out["artifacts"]["execution_plan"]["preflight"]
        self.assertEqual(preflight["status"], "NEEDS_CAPABILITY_EXTENSION")
        mapping = out["artifacts"]["analysis_plan"]["objective_capability_map"]
        self.assertFalse(mapping[0]["supported"])

    def test_pgc_only_marks_ugc_not_applicable_through_round2(self):
        self.st.write_artifact("confirmed_plan", {
            "confirmed": True,
            "content_scope": "pgc",
            "selected_modules": {
                "core_plus_conditional": [
                    "brand_context", "activity_performance", "evidence_summary",
                ],
            },
        })
        ugc = handlers.ugc_discovery({"id": "n08", "name": "ugc"}, self.st, {})
        ugc_art = ugc["artifacts"]["ugc_category_list"]
        self.assertTrue(ugc_art["not_applicable"])
        self.assertEqual(ugc_art["categories"], [])
        self.st.write_artifact("ugc_category_list", ugc_art)
        self.st.write_artifact("annotated_round1", {
            "rows": [{
                "id": "1", "title": "品牌内容", "content": "官方发布",
                "content_mode": "PGC", "ai_sentiment": 1, "relevance": True,
            }],
        })
        self.st.write_artifact("confirmed_opinion_labels", {
            "labels": ["品牌体验"], "ugc_categories": [],
            "ugc_not_applicable": True,
        })
        annotated = {
            "round": 2,
            "rows": [{
                "id": "1", "title": "品牌内容", "content": "官方发布",
                "content_mode": "PGC", "ai_sentiment": 1, "relevance": True,
                "opinion": "品牌体验", "ugc_category": "N/A",
                "discussion_dimension": "品牌",
            }],
            "coverage": {
                "has_error": False,
                "ugc_category": {"status": "NOT_APPLICABLE", "missing": 0},
            },
            "not_applicable": ["ugc_category"],
            "degraded": False,
        }
        with patch.object(
            handlers.annotate_mod, "annotate_round2", return_value=annotated
        ):
            out = handlers.annotate(
                {"id": "n11", "outputs": ["annotated_data"]}, self.st, {}
            )
        result = out["artifacts"]["annotated_data"]
        self.assertEqual(result["rows"][0]["ugc_category"], "N/A")
        self.assertEqual(
            result["coverage"]["ugc_category"]["status"], "NOT_APPLICABLE"
        )
        self.assertIn("ugc_category", result["not_applicable"])

    def test_run_init_full_rejects_incomplete_resource_manifest(self):
        self.st.write_artifact("requirement_confirmed", {
            "confirmed": True,
            "brand": "测试品牌",
            "activity_name": "测试活动",
            "period_start": "2026-01-01",
            "period_end": "2026-01-07",
            "platforms": ["xiaohongshu"],
            "brand_query": "测试品牌",
            "activity_query": "测试活动",
            "content_scope": "all",
            "business_questions": ["活动表现与改进"],
            "delivery_expectation": "飞书营销活动复盘报告",
            "allowed_actions": "调用已登记能力",
            "source_file": {"name": "test.xlsx", "mount_path": "test.xlsx"},
        })
        plan_out = handlers.plan_compile({"id": "n02", "name": "plan"}, self.st, {})
        for key, payload in plan_out["artifacts"].items():
            self.st.write_artifact(key, payload)
        self.st.human = {"node": "n03_hc_plan", "expect": ["confirmed_plan"]}
        self.st.write_artifact(
            "confirmed_plan",
            hc_bridge.build_human_outputs(self.st, "pass", "")["confirmed_plan"],
        )
        self.st.params["run_mode"] = "full"
        source_path = Path(self.ws) / "test.xlsx"
        source_path.write_bytes(b"xlsx-fixture")
        input_data = {
            "rows": [{"id": "1"}],
            "meta": {
                "platforms": ["xiaohongshu"],
                "campaigns": ["测试活动"],
                "source_row_count": 1,
                "accepted_row_count": 1,
                "period_start": "20260101",
                "period_end": "20260107",
            },
        }
        with patch("excel_source.resolve_activity_xlsx", return_value=source_path), \
                patch("excel_source.load_activity_rows", return_value=input_data):
            with self.assertRaisesRegex(ValueError, "全部实际资源版本"):
                handlers.run_init(
                    {"id": "n04", "outputs": ["run_record"]},
                    self.st,
                    {"resource_versions": {"model": "m1"}},
                )
    def test_plan_compile_requires_confirmed_requirement(self):
        with self.assertRaisesRegex(ValueError, "尚未确认"):
            handlers.plan_compile({"id": "n02", "name": "方案编制"}, self.st, {})

    def test_qc_decision_routes(self):
        node = {"id": "n18", "routes": {"no_p0": "a", "has_p0_retry": "b", "has_p0_maxed": "c"}}
        # no qc_report -> no_p0
        self.assertEqual(handlers.qc_decision(node, self.st, {})["route"], "no_p0")
        # has_p0, under max -> retry
        self.st.write_artifact("qc_report", {"has_p0": True})
        self.st.params["qc_max_rounds"] = 3
        self.st.counters["qc_rounds"] = 0
        self.assertEqual(handlers.qc_decision(node, self.st, {})["route"], "has_p0_retry")
        # has_p0, at max -> maxed
        self.st.counters["qc_rounds"] = 3
        self.assertEqual(handlers.qc_decision(node, self.st, {})["route"], "has_p0_maxed")

    def test_user_decision_requires_explicit_status(self):
        node = {"id": "n22", "routes": {"confirmed": "a", "issues": "b", "escalate": "c"}}
        with self.assertRaisesRegex(ValueError, "缺失"):
            handlers.user_decision(node, self.st, {})
        self.st.write_artifact("user_confirmation", {"status": "issues"})
        self.assertEqual(handlers.user_decision(node, self.st, {})["route"], "issues")

    def test_fix_report_increments_counters(self):
        with self.assertRaisesRegex(RuntimeError, "无法执行报告返修"):
            handlers.fix_report({"id": "n19"}, self.st, {})
        self.assertEqual(self.st.counters["qc_rounds"], 1)
        self.assertEqual(self.st.counters["report_patch"], 1)


class TestSchemaCoverageAllNodes(unittest.TestCase):
    """遍历真实 DAG 所有节点,按 mode 校验 handler 契约。"""

    @classmethod
    def setUpClass(cls):
        cls.dag = load_dag()

    def setUp(self):
        self.ws = tempfile.mkdtemp()
        self.st = make_state(self.ws)
        # pre-seed artifacts some handlers read, so code paths are exercised
        self.st.params["ugc_category_mode"] = "fixed_four"
        self.st.write_artifact("requirement_confirmed", {
            "confirmed": True,
            "brand": "测试品牌",
            "activity_name": "测试活动",
            "period_start": "2026-01-01",
            "period_end": "2026-01-07",
            "platforms": ["xiaohongshu"],
            "brand_query": "测试品牌",
            "activity_query": "测试活动",
            "content_scope": "all",
            "business_questions": ["活动表现与改进"],
            "delivery_expectation": "飞书营销活动复盘报告",
            "allowed_actions": "调用已登记能力",
            "source_file": {"name": "test.xlsx", "mount_path": "test.xlsx"},
        })
        plan_out = handlers.plan_compile(
            {"id": "n02", "name": "plan"}, self.st, {}
        )
        for key, payload in plan_out["artifacts"].items():
            self.st.write_artifact(key, payload)
        self.st.human = {"node": "n03_hc_plan", "expect": ["confirmed_plan"]}
        self.st.write_artifact(
            "confirmed_plan",
            hc_bridge.build_human_outputs(self.st, "pass", "")["confirmed_plan"],
        )
        self.st.write_artifact("user_confirmation", {"status": "confirmed"})

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    def test_every_node_handler_honors_mode_contract(self):
        with compliant_pipeline_services():
            for node in self.dag.spec["nodes"]:
                mode = node.get("mode")
                fn = handlers.get(node["handler"])
                with self.subTest(node=node["id"], mode=mode):
                    if mode == "human" or node.get("pause_capable"):
                        with self.assertRaises(HumanPause):
                            fn(node, self.st, {})
                    elif mode == "decision":
                        res = fn(node, self.st, {})
                        self.assertIn("route", res)
                        self.assertIn(res["route"], node["routes"])
                    else:
                        res = fn(node, self.st, {})
                        self.assertIn("artifacts", res)
                        outputs = node.get("outputs", [])
                        for key in outputs:
                            self.assertIn(key, res["artifacts"],
                                          f"{node['id']} missing output {key}")


if __name__ == "__main__":
    unittest.main()
