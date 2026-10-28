"""波次6：HC 桥接层（Gateway ↔ 引擎人工关卡契约）测试。

验证：
  - parse_decision 解析 `HCx [pass/reject/remark]: note` 格式；
  - remark 无内容 → build_human_outputs 返回 None（只记录不续跑，project_memory 硬约束）；
  - n17 关卡：pass 用 Validator 全量扫描预填 qc_report；reject 追加人工 P0 驱动 n19_fix；
  - n21 关卡：pass→confirmed / reject→issues / escalate→escalate；
  - 全流程用 hc_bridge 驱动所有人工关卡跑到 completed（模拟 Gateway resume_job 回路）；
  - hc_pending.json 落盘 / 清理。
"""
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tempfile
import shutil
import unittest
import base64
import json
from pathlib import Path
from unittest.mock import patch

import engine
import hc_bridge
import handlers
from _util import compliant_pipeline_services, load_dag, make_state


def _g1_note() -> str:
    return json.dumps(
        {
            "brand": "瑞幸",
            "activity_name": "瑞幸×暹罗厘普联名活动",
            "period_start": "2026-09-28",
            "period_end": "2026-10-07",
            "platforms": ["xiaohongshu"],
            "brand_query": "瑞幸",
            "brand_filter": "",
            "activity_query": "瑞幸+(暹罗厘普|多栋|厘普|比格)",
            "activity_filter": "",
            "product_query": "瑞幸+流心芝士拿铁",
            "content_scope": "all",
            "business_questions": ["活动表现", "内容经验", "后续建议"],
            "delivery_expectation": "飞书营销活动复盘报告",
            "allowed_actions": "调用已登记能力",
            "brand_words": ["瑞幸"],
            "product_words": ["流心芝士拿铁"],
            "keywords": ["瑞幸"],
            "campaigns": [
                {
                    "id": "瑞幸×暹罗厘普联名活动",
                    "filter_words": ["暹罗厘普", "多栋", "厘普", "比格"],
                }
            ],
            "source_file": {
                "name": "瑞幸暹罗厘普联名数据.xlsx",
                "mount_path": "topic5_activity_detail_job_x.xlsx",
            },
        },
        ensure_ascii=False,
    )


def _write_qc(st, *, has_p0=False) -> None:
    payload = {
        "validator": "topic5-validator",
        "validation_id": "validation-test",
        "rules_version": "test",
        "validator_version": "topic5-validator@test",
        "execution_source": "ma-session-test",
        "execution_ref": {
            "task_id": st.task_id,
            "run_id": st.run_id,
            "attempt_id": "attempt-test",
        },
        "qc_metadata": {
            "mode": "independent-agent",
            "validator_id": "topic5-validator",
            "input_artifacts": [
                {
                    "key": key,
                    "artifact_id": f"{key}@v1",
                    "version": 1,
                    "sha256": f"sha-{key}",
                }
                for key in (
                    "report_md", "requirement_confirmed", "stats_bundle",
                    "chart_urls", "validator_rules",
                )
            ],
        },
        "subject": st.artifact_ref("report_md"),
        "has_p0": has_p0,
        "p0_count": 1 if has_p0 else 0,
        "p1_count": 0,
        "p2_count": 0,
        "issues": (
            [{"id": "P0-test", "severity": "P0", "title": "test"}]
            if has_p0 else []
        ),
    }
    path = st.run_dir / "validation" / "qc_report.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class TestParseDecision(unittest.TestCase):
    def test_pass_with_note(self):
        self.assertEqual(hc_bridge.parse_decision("HC3 [pass]: 看起来没问题"),
                         ("pass", "看起来没问题"))

    def test_reject(self):
        d, n = hc_bridge.parse_decision("G3 [reject]: 第二章数字对不上")
        self.assertEqual(d, "reject")
        self.assertEqual(n, "第二章数字对不上")

    def test_remark_empty(self):
        self.assertEqual(hc_bridge.parse_decision("remark:"), ("remark", ""))

    def test_malformed_falls_back_remark(self):
        d, n = hc_bridge.parse_decision("随便写点什么")
        self.assertEqual(d, "remark")
        self.assertEqual(n, "随便写点什么")


class TestHcPendingFile(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp()
        self.st = make_state(self.ws, task_id="t5")
        self.st.human = {"node": "n17_validate", "prompt": "质检关卡",
                         "expect": ["qc_report"]}

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    def test_write_and_clear(self):
        payload = hc_bridge.write_hc_pending(self.st, self.st.run_dir)
        self.assertEqual(payload["hc_kind"], "G3")
        self.assertEqual(payload["node"], "n17_validate")
        p = self.st.run_dir / "hc_pending.json"
        self.assertTrue(p.exists())
        hc_bridge.clear_hc_pending(self.st.run_dir)
        self.assertFalse(p.exists())

    def test_g2_pending_contains_complete_review_context(self):
        self.st.human = {
            "node": "n03_hc_plan",
            "prompt": "方案确认",
            "expect": ["confirmed_plan"],
        }
        self.st.write_artifact("analysis_plan", {"title": "活动复盘方案"})
        self.st.write_artifact("execution_plan", {"stages": [{"name": "数据准备"}]})
        self.st.write_artifact("selected_modules", {"core": ["brand_context"]})
        payload = hc_bridge.write_hc_pending(self.st, self.st.run_dir)
        context = payload["review_context"]
        self.assertEqual(context["analysis_plan"]["title"], "活动复盘方案")
        self.assertEqual(context["execution_plan"]["stages"][0]["name"], "数据准备")
        self.assertEqual(context["selected_modules"]["core"], ["brand_context"])


class TestBuildHumanOutputs(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp()
        self.st = make_state(self.ws, task_id="t5")
        self.st.write_artifact("report_md", {
            "markdown": ("# 复盘\n## 品牌传播\nok\n## 活动传播\nok\n"
                         "## 综合定位\nok\n## 经验总结\nok\n"),
            "version": "0.0", "degraded": True})

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    def test_remark_empty_returns_none(self):
        self.st.human = {"node": "n17_validate", "expect": ["qc_report"]}
        self.assertIsNone(hc_bridge.build_human_outputs(self.st, "remark", ""))

    def test_stale_g3_cannot_reject_current_g4(self):
        self.st.human = {
            "node": "n21_hc_user",
            "expect": ["user_confirmation"],
        }
        with self.assertRaisesRegex(ValueError, "请求 G3，当前等待 G4"):
            hc_bridge.build_human_outputs(
                self.st,
                "reject",
                "stale validator result",
                requested_hc="G3",
            )

    def test_n17_pass_prefills_qc(self):
        self.st.human = {"node": "n17_validate", "expect": ["qc_report"]}
        _write_qc(self.st)
        out = hc_bridge.build_human_outputs(self.st, "pass", "")
        self.assertIn("qc_report", out)
        self.assertFalse(out["qc_report"]["has_p0"])

    def test_n17_reject_injects_p0(self):
        self.st.human = {"node": "n17_validate", "expect": ["qc_report"]}
        _write_qc(self.st)
        out = hc_bridge.build_human_outputs(self.st, "reject", "数字对不上")
        self.assertTrue(out["qc_report"]["has_p0"])
        self.assertTrue(any(i["id"] == "P0-human-reject"
                            for i in out["qc_report"]["issues"]))

    def test_n21_decisions(self):
        self.st.human = {"node": "n21_hc_user", "expect": ["user_confirmation"]}
        self.st.write_artifact("delivery_draft", {
            "state": "ready", "revision_id": 1, "report_version": "0.0",
        })
        with patch("hc_bridge.feishu.current_revision", return_value=1):
            self.assertEqual(
                hc_bridge.build_human_outputs(
                    self.st, "pass", "")["user_confirmation"]["status"],
                "confirmed")
        self.assertEqual(
            hc_bridge.build_human_outputs(self.st, "reject", "改")["user_confirmation"]["status"],
            "issues")
        self.assertEqual(
            hc_bridge.build_human_outputs(self.st, "escalate", "")["user_confirmation"]["status"],
            "escalate")

    def test_post_publish_recovery_is_system_owned_and_routes_to_fix(self):
        self.st.status = "awaiting_human"
        self.st.human = {
            "node": "n21_hc_user",
            "expect": ["user_confirmation"],
        }
        self.st.write_artifact("qc_report", {
            "has_p0": False,
            "p0_count": 0,
            "issues": [{"id": "P1-existing", "severity": "P1"}],
        })
        self.st.write_artifact("delivery_draft", {
            "state": "needs_review",
            "revision_id": 244,
            "post_publish_validation": {
                "has_p0": True,
                "p0_count": 1,
                "issues": [{
                    "id": "P0-postpublish-images",
                    "severity": "P0",
                }],
            },
        })

        out = hc_bridge.build_post_publish_recovery_outputs(self.st)

        self.assertEqual(out["user_confirmation"]["authority"], "system")
        self.assertEqual(out["user_confirmation"]["status"], "auto_rejected")
        self.assertEqual(out["_control"]["next"], "n19_fix")
        self.assertEqual(out["_control"]["gate_status"], "auto_rejected")
        self.assertTrue(out["qc_report"]["has_p0"])
        self.assertEqual(out["qc_report"]["p0_count"], 1)
        self.assertIn(
            "P0-postpublish-images",
            [issue["id"] for issue in out["qc_report"]["issues"]],
        )

    def test_post_publish_recovery_rejects_ready_draft(self):
        self.st.status = "awaiting_human"
        self.st.human = {
            "node": "n21_hc_user",
            "expect": ["user_confirmation"],
        }
        self.st.write_artifact("delivery_draft", {
            "state": "ready",
            "post_publish_validation": {
                "has_p0": False,
                "p0_count": 0,
                "issues": [],
            },
        })
        with self.assertRaisesRegex(ValueError, "没有发布后 P0"):
            hc_bridge.build_post_publish_recovery_outputs(self.st)

    def test_g1_structured_requirement_and_g2_inheritance(self):
        self.st.human = {
            "node": "n01_hc_requirement",
            "expect": ["requirement_confirmed"],
        }
        g1_payload = json.loads(_g1_note())
        g1_payload.pop("source_file")
        requirement = hc_bridge.build_human_outputs(
            self.st, "pass", json.dumps(g1_payload, ensure_ascii=False)
        )["requirement_confirmed"]
        self.assertTrue(requirement["confirmed"])
        self.assertEqual(requirement["brand"], "瑞幸")
        self.assertNotIn("source_file", requirement)
        self.st.write_artifact("requirement_confirmed", requirement)
        plan_out = handlers.plan_compile(
            {"id": "n02_plan", "name": "方案编制"}, self.st, {}
        )
        for key, payload in plan_out["artifacts"].items():
            self.st.write_artifact(key, payload)
        self.assertEqual(
            plan_out["artifacts"]["execution_plan"]["preflight"]["status"],
            "READY_PENDING_INPUT",
        )
        self.st.human = {
            "node": "n03_hc_plan",
            "expect": ["confirmed_plan"],
        }
        plan = hc_bridge.build_human_outputs(
            self.st,
            "pass",
            json.dumps(
                {
                    "source_file": {
                        "name": "瑞幸暹罗厘普联名数据.xlsx",
                        "mount_path": "topic5_activity_detail_job_x.xlsx",
                    }
                },
                ensure_ascii=False,
            ),
        )["confirmed_plan"]
        self.assertEqual(plan["brand"], "瑞幸")
        self.assertEqual(
            plan["source_file"]["mount_path"],
            "topic5_activity_detail_job_x.xlsx",
        )
        self.assertEqual(plan["version"], "plan-v1")
        self.assertIn("瑞幸×暹罗厘普联名活动", plan["analysis_plan"]["title"])
        self.assertEqual(plan["execution_plan"]["stages"][0]["name"], "数据准备")
        self.assertEqual(
            plan["selected_modules"]["core"],
            ["brand_context", "activity_performance", "evidence_summary"],
        )

    def test_g2_rejects_resume_without_activity_file_reference(self):
        requirement = json.loads(_g1_note())
        requirement.pop("source_file")
        requirement["confirmed"] = True
        self.st.write_artifact("requirement_confirmed", requirement)
        plan_out = handlers.plan_compile(
            {"id": "n02_plan", "name": "方案编制"}, self.st, {}
        )
        for key, payload in plan_out["artifacts"].items():
            self.st.write_artifact(key, payload)
        self.st.human = {
            "node": "n03_hc_plan",
            "expect": ["confirmed_plan"],
        }
        with self.assertRaisesRegex(ValueError, "缺少活动明细文件引用"):
            hc_bridge.build_human_outputs(self.st, "pass", "")

    def test_g1_accepts_requirement_without_product_dimension(self):
        self.st.human = {
            "node": "n01_hc_requirement",
            "expect": ["requirement_confirmed"],
        }
        payload = json.loads(_g1_note())
        payload.pop("product_query")
        payload["product_words"] = []
        requirement = hc_bridge.build_human_outputs(
            self.st, "pass", json.dumps(payload, ensure_ascii=False)
        )["requirement_confirmed"]
        self.assertTrue(requirement["confirmed"])
        self.assertNotIn("product_query", requirement)

    def test_g1_rejects_invalid_payload(self):
        self.st.human = {
            "node": "n01_hc_requirement",
            "expect": ["requirement_confirmed"],
        }
        with self.assertRaisesRegex(ValueError, "有效 JSON"):
            hc_bridge.build_human_outputs(self.st, "pass", "not-json")

    def test_g1_accepts_base64_transport(self):
        self.st.human = {
            "node": "n01_hc_requirement",
            "expect": ["requirement_confirmed"],
        }
        encoded = base64.urlsafe_b64encode(
            _g1_note().encode("utf-8")
        ).decode("ascii")
        out = hc_bridge.build_human_outputs(
            self.st, "pass", "b64:" + encoded
        )
        self.assertEqual(out["requirement_confirmed"]["brand"], "瑞幸")

    def test_g1_accepts_mounted_requirement_file(self):
        self.st.human = {
            "node": "n01_hc_requirement",
            "expect": ["requirement_confirmed"],
        }
        with patch.object(Path, "read_text", return_value=_g1_note()):
            out = hc_bridge.build_human_outputs(
                self.st,
                "pass",
                "@/mnt/session/uploads/topic5_requirement_job_1.json",
            )
        self.assertEqual(out["requirement_confirmed"]["period_end"], "2026-10-07")

    def test_g1_rejects_invalid_iso_date(self):
        self.st.human = {
            "node": "n01_hc_requirement",
            "expect": ["requirement_confirmed"],
        }
        payload = json.loads(_g1_note())
        payload["period_end"] = "202b-10-07"
        with self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
            hc_bridge.build_human_outputs(
                self.st, "pass", json.dumps(payload, ensure_ascii=False)
            )


class TestEndToEndViaBridge(unittest.TestCase):
    """用 hc_bridge 驱动所有人工关卡跑到 completed（模拟 Gateway resume 回路）。"""

    def setUp(self):
        for k in ("FEISHU_USER_ACCESS_TOKEN", "FEISHU_APP_ID", "FEISHU_APP_SECRET"):
            os.environ.pop(k, None)
        self.ws = tempfile.mkdtemp()
        self.dag = load_dag()

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    def test_full_run_passes_all_gates(self):
        st = engine.create_run(self.dag, workspace=Path(self.ws),
                               task_id="t5", run_id="bridge_e2e")
        st.params["run_mode"] = "demo"
        with compliant_pipeline_services():
            engine.run(self.dag, st)
            steps = 0
            while st.status == "awaiting_human" and steps < 60:
                # G1 提供结构化问卷；其余人工门默认 pass。
                hc_bridge.write_hc_pending(st, st.run_dir)
                note = (
                    _g1_note()
                    if st.human["node"] == "n01_hc_requirement"
                    else ""
                )
                if st.human["node"] == "n17_validate":
                    _write_qc(st)
                outs = hc_bridge.build_human_outputs(st, "pass", note)
                hc_bridge.clear_hc_pending(st.run_dir)
                engine.resume(self.dag, st, human_outputs=outs or {})
                steps += 1
        self.assertEqual(st.status, "completed")
        ho = st.read_artifact("handoff")
        self.assertTrue(ho["terminal_accepted"])
        self.assertTrue(ho["validated"])
        self.assertNotIn("demo_validated", ho)


if __name__ == "__main__":
    unittest.main()
