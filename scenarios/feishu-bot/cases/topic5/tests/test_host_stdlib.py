"""Topic5 Gateway host-side 纯 stdlib 组件冒烟测试。

覆盖不依赖 arkagent 框架的三件套:pipeline_store / topic5_progress_card / config。
gateway.py / topic5_runner.py / topic5_hitl.py 依赖 ``arkagent.*``,是 host-only,
无法在沙箱内独立 import,因此不在此测试(见各文件头部注释)。

运行:cd scenarios/feishu-bot/cases/topic5 && python3 -m unittest tests.test_host_stdlib
"""
from __future__ import annotations

import asyncio
import io
import importlib.util
import json
import re
import sys
import sqlite3
import tempfile
import types
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

# 让 `import pipeline_store` 等按 topic6 的扁平布局工作。
_CASE_DIR = Path(__file__).resolve().parent.parent
if str(_CASE_DIR) not in sys.path:
    sys.path.insert(0, str(_CASE_DIR))

import config  # noqa: E402
import pipeline_store as ps  # noqa: E402
import topic5_intake as intake  # noqa: E402
import topic5_progress_card as card  # noqa: E402


class PipelineStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = ps.PipelineStore(str(Path(self._tmp.name) / "t5.db"))

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def test_create_and_fetch_job(self) -> None:
        job = self.store.create_job(
            "chat_1", "", "ou_user", "sess_1", "full", "/workspace/tasks/t/runs/r"
        )
        self.assertEqual(job.status, ps.STATUS_RUNNING)
        self.assertEqual(job.current_phase, "intake")
        got = self.store.get_job(job.job_id)
        self.assertIsNotNone(got)
        self.assertEqual(got.ma_session_id, "sess_1")
        # 会话键唯一活跃
        active = self.store.get_active_job_by_session_key("chat_1", "", "ou_user")
        self.assertEqual(active.job_id, job.job_id)
        # 全局活跃
        self.assertEqual(self.store.get_active_job().job_id, job.job_id)

    def test_lifecycle_transitions(self) -> None:
        job = self.store.create_job("c", "", "u", "s", "demo", "d")
        self.store.update_phase(job.job_id, "clean")
        self.assertEqual(self.store.get_job(job.job_id).current_phase, "clean")
        self.store.mark_wait_hc(job.job_id, "G1")
        j = self.store.get_job(job.job_id)
        self.assertEqual(j.status, ps.STATUS_WAIT_HC)
        self.assertEqual(j.current_phase, "G1")
        self.store.resume_running(job.job_id, "aggregate")
        self.assertEqual(self.store.get_job(job.job_id).status, ps.STATUS_RUNNING)
        self.store.mark_done(job.job_id, online_url="https://feishu/docx/abc")
        j = self.store.get_job(job.job_id)
        self.assertEqual(j.status, ps.STATUS_DONE)
        self.assertEqual(j.online_url, "https://feishu/docx/abc")

    def test_resumable_includes_failed(self) -> None:
        job = self.store.create_job("c", "", "u", "s", "full", "d")
        self.store.mark_failed(job.job_id, "sse_dropped")
        # 活跃查询拿不到
        self.assertIsNone(self.store.get_active_job_by_session_key("c", "", "u"))
        # 可续跑查询能捞回
        res = self.store.get_resumable_job_by_session_key("c", "", "u")
        self.assertIsNotNone(res)
        self.assertEqual(res.job_id, job.job_id)

    def test_hc_event_flow(self) -> None:
        job = self.store.create_job("c", "", "u", "s", "full", "d")
        payload = {"hc": "G3", "node": "n17_validate", "prompt": "请终审"}
        eid = self.store.append_hc_event(job.job_id, "G3", payload)
        self.store._conn.execute(  # 写回 card_message_id
            "UPDATE pipeline_hc_events SET card_message_id = ? WHERE id = ?",
            ("om_123", eid),
        )
        by_card = self.store.get_pending_hc_by_card("om_123")
        self.assertEqual(by_card.id, eid)
        self.assertEqual(by_card.hc_kind, "G3")
        self.store.resolve_hc_event(eid, "pass", "看着没问题", operator_label="张三")
        resolved = self.store.get_hc_event(eid)
        self.assertEqual(resolved.user_decision, "pass")
        self.assertEqual(resolved.operator_label, "张三")
        self.assertIsNotNone(resolved.resolved_at)
        self.assertEqual(len(self.store.list_resolved_hc_for_job(job.job_id)), 1)

    def test_latest_hc_uses_id_tiebreak_and_claim_is_single_winner(self) -> None:
        job = self.store.create_job("c", "", "u", "s", "full", "d")
        first = self.store.append_hc_event(job.job_id, "G3", {"version": 1})
        second = self.store.append_hc_event(job.job_id, "G3", {"version": 2})
        self.store._conn.execute(
            "UPDATE pipeline_hc_events SET created_at = 100 WHERE id IN (?, ?)",
            (first, second),
        )
        self.store.mark_wait_hc(job.job_id, "G3")
        self.assertEqual(
            self.store.latest_hc_for_job(job.job_id, "G3").id, second
        )
        self.assertFalse(self.store.claim_hc_event(first, "pass"))
        self.assertTrue(self.store.claim_hc_event(second, "pass"))
        self.assertFalse(self.store.claim_hc_event(second, "reject"))

    def test_g4_reminder_recomputes_escalation_from_actual_reminder(self) -> None:
        job = self.store.create_job("c", "", "u", "s", "full", "d")
        event_id = self.store.append_hc_event(job.job_id, "G4", {})
        self.store.mark_wait_hc(job.job_id, "G4")
        friday = 1_799_968_400  # 2027-01-15 local date; exact hour is irrelevant.
        self.assertTrue(self.store.mark_g4_reminded(event_id, friday))
        event = self.store.get_hc_event(event_id)
        self.assertEqual(event.reminded_at, friday)
        self.assertGreater(event.escalate_at, friday)
        self.assertFalse(self.store.mark_g4_reminded(event_id, friday + 1))

    def test_hc_kind_whitelist(self) -> None:
        job = self.store.create_job("c", "", "u", "s", "full", "d")
        with self.assertRaises(ValueError):
            self.store.append_hc_event(job.job_id, "HC1", {})  # topic6 的门,非法

    def test_schema_idempotent_reopen(self) -> None:
        path = str(Path(self._tmp.name) / "reopen.db")
        s1 = ps.PipelineStore(path)
        j = s1.create_job("c", "", "u", "s", "full", "d")
        s1.close()
        s2 = ps.PipelineStore(path)  # 复用老库不报错
        self.assertEqual(s2.get_job(j.job_id).job_id, j.job_id)
        s2.close()

    def test_thread_mode_fields(self) -> None:
        # 话题模式:create_job 带触发消息 id,set_topic_thread_id 回写话题键。
        job = self.store.create_job(
            "chat_1", "", "ou_user", "sess_1", "full", "d",
            root_message_id="om_trigger",
        )
        self.assertEqual(job.root_message_id, "om_trigger")
        self.assertEqual(job.topic_thread_id, "")
        self.store.set_topic_thread_id(job.job_id, "omt_thread_1")
        got = self.store.get_job(job.job_id)
        self.assertEqual(got.root_message_id, "om_trigger")
        self.assertEqual(got.topic_thread_id, "omt_thread_1")
        # 新话题内的文件/命令仍应命中触发时创建的 job。
        active = self.store.get_active_job_by_session_key(
            "chat_1", "omt_thread_1", "ou_user"
        )
        self.assertEqual(active.job_id, job.job_id)
        self.assertIsNone(
            self.store.get_active_job_by_session_key(
                "chat_1", "", "ou_user"
            )
        )
        # 话题隔离不依赖发起人；同话题里其他成员再次触发也能识别已有任务。
        by_topic = self.store.get_active_job_by_topic(
            "chat_1", "omt_thread_1"
        )
        self.assertEqual(by_topic.job_id, job.job_id)

    def test_multiple_topics_can_have_active_jobs(self) -> None:
        first = self.store.create_job(
            "chat_1", "", "ou_a", "sess_a", "full", "d",
            root_message_id="om_a",
        )
        second = self.store.create_job(
            "chat_1", "", "ou_b", "sess_b", "demo", "d",
            root_message_id="om_b",
        )
        self.store.set_topic_thread_id(first.job_id, "omt_a")
        self.store.set_topic_thread_id(second.job_id, "omt_b")
        self.assertEqual(
            self.store.get_active_job_by_topic("chat_1", "omt_a").job_id,
            first.job_id,
        )
        self.assertEqual(
            self.store.get_active_job_by_topic("chat_1", "omt_b").job_id,
            second.job_id,
        )
        # 主时间线不是共享互斥键，每条触发消息将自行创建新话题。
        self.assertIsNone(
            self.store.get_active_job_by_topic("chat_1", "")
        )

    def test_source_file_fields(self) -> None:
        job = self.store.create_job("c", "", "u", "s", "full", "d")
        self.store.set_source_file(
            job.job_id,
            "file_1",
            "瑞幸暹罗厘普联名数据.xlsx",
            "topic5_activity_detail_job_1.xlsx",
        )
        got = self.store.get_job(job.job_id)
        self.assertEqual(got.source_file_id, "file_1")
        self.assertEqual(got.source_file_name, "瑞幸暹罗厘普联名数据.xlsx")
        self.assertEqual(
            got.source_file_mount_path, "topic5_activity_detail_job_1.xlsx"
        )

    def test_wait_input_job_remains_active_across_lookup_paths(self) -> None:
        job = self.store.create_job("c", "thread", "u", "s", "full", "d")
        self.store.mark_wait_input(job.job_id)
        got = self.store.get_job(job.job_id)
        self.assertEqual(got.status, ps.STATUS_WAIT_INPUT)
        self.assertEqual(got.current_phase, "input")
        self.assertEqual(
            self.store.get_active_job_by_session_key(
                "c", "thread", "u"
            ).job_id,
            job.job_id,
        )
        self.assertIn(job.job_id, {item.job_id for item in self.store.list_active_jobs()})

    def test_old_db_alter_adds_thread_columns(self) -> None:
        # 老库(无话题字段)重开后应自动补列,读回默认空串而非抛错。
        path = str(Path(self._tmp.name) / "legacy.db")
        legacy = sqlite3.connect(path)
        legacy.executescript(
            """
            CREATE TABLE pipeline_jobs (
                job_id TEXT PRIMARY KEY, chat_id TEXT NOT NULL,
                thread_id TEXT NOT NULL DEFAULT '', user_open_id TEXT NOT NULL,
                ma_session_id TEXT NOT NULL, mode TEXT NOT NULL,
                project_dir TEXT NOT NULL, current_phase TEXT NOT NULL DEFAULT 'intake',
                status TEXT NOT NULL DEFAULT 'running',
                started_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
                finished_at INTEGER, last_error TEXT NOT NULL DEFAULT '',
                online_url TEXT NOT NULL DEFAULT ''
            );
            INSERT INTO pipeline_jobs
                (job_id, chat_id, user_open_id, ma_session_id, mode, project_dir,
                 started_at, updated_at)
            VALUES ('job_old', 'c', 'u', 's', 'full', 'd', 1, 1);
            """
        )
        legacy.commit()
        legacy.close()
        store = ps.PipelineStore(path)  # 触发 ALTER 补列
        got = store.get_job("job_old")
        self.assertEqual(got.root_message_id, "")
        self.assertEqual(got.topic_thread_id, "")
        self.assertEqual(got.source_file_id, "")
        self.assertEqual(got.source_file_name, "")
        self.assertEqual(got.source_file_mount_path, "")
        store.set_topic_thread_id("job_old", "omt_x")
        self.assertEqual(store.get_job("job_old").topic_thread_id, "omt_x")
        store.close()


class IntakeFormTest(unittest.TestCase):
    def test_normalizes_luckin_requirement(self) -> None:
        requirement = intake.normalize_requirement(
            {
                "brand": "瑞幸",
                "activity_name": "瑞幸×暹罗厘普联名活动",
                "period_start": "2026-09-28 +0800",
                "period_end": "2026-10-07 +0800",
                "platforms": [{"value": "xiaohongshu"}],
                "brand_query": "瑞幸",
                "brand_filter": "无",
                "activity_query": "瑞幸+(暹罗厘普|多栋|厘普|比格)",
                "activity_filter": "无",
                "product_query": "瑞幸+流心芝士拿铁",
                "business_questions": "活动表现与改进",
                "delivery_expectation": "飞书营销活动复盘报告",
                "allowed_actions": "调用已登记能力",
                "content_scope": {"value": "all"},
            }
        )
        self.assertEqual(requirement["platforms"], ["xiaohongshu"])
        self.assertEqual(requirement["period_start"], "2026-09-28")
        self.assertEqual(requirement["period_end"], "2026-10-07")
        self.assertEqual(requirement["brand_filter"], "")
        self.assertEqual(requirement["brand_words"], ["瑞幸"])
        self.assertEqual(requirement["product_words"], ["流心芝士拿铁"])
        self.assertEqual(
            requirement["campaigns"][0]["filter_words"],
            ["暹罗厘普", "多栋", "厘普", "比格"],
        )

    def test_product_query_is_optional_for_two_level_funnel(self) -> None:
        raw = dict(intake.DEMO_REQUIREMENT_DEFAULTS)
        raw.pop("product_query")
        requirement = intake.normalize_requirement(raw)
        self.assertEqual(requirement["product_query"], "")
        self.assertEqual(requirement["product_words"], [])

        form = intake.build_requirement_form(
            7, lambda decision: {"decision": decision}
        )
        product = next(
            element for element in form["elements"]
            if element.get("name") == "product_query"
        )
        self.assertFalse(product["required"])

    def test_g1_context_fields_are_optional_and_use_system_defaults(
        self,
    ) -> None:
        raw = {
            key: value
            for key, value in intake.DEMO_REQUIREMENT_DEFAULTS.items()
            if key not in intake.OPTIONAL_REQUIREMENT_DEFAULTS
        }
        requirement = intake.normalize_requirement(raw)
        for key, expected in intake.OPTIONAL_REQUIREMENT_DEFAULTS.items():
            with self.subTest(key=key):
                if key == "business_questions":
                    self.assertEqual(
                        requirement[key],
                        ["活动实际表现如何", "哪些内容和参与方式有效", "下次如何改进"],
                    )
                else:
                    self.assertEqual(requirement[key], expected)

        form = intake.build_requirement_form(
            7, lambda decision: {"decision": decision}
        )
        by_name = {
            element.get("name"): element for element in form["elements"]
        }
        for key in intake.OPTIONAL_REQUIREMENT_DEFAULTS:
            with self.subTest(key=key):
                self.assertFalse(by_name[key]["required"])

    def test_rejects_missing_or_reversed_dates(self) -> None:
        with self.assertRaises(intake.IntakeValidationError):
            intake.normalize_requirement({})
        with self.assertRaisesRegex(
            intake.IntakeValidationError, "开始日期不能晚于结束日期"
        ):
            intake.normalize_requirement(
                {
                    "brand": "瑞幸",
                    "activity_name": "活动",
                    "period_start": "2026-10-08",
                    "period_end": "2026-10-07",
                    "platforms": ["xiaohongshu"],
                    "brand_query": "瑞幸",
                    "activity_query": "联名",
                    "product_query": "拿铁",
                    "business_questions": "活动表现与改进",
                    "delivery_expectation": "飞书营销活动复盘报告",
                    "allowed_actions": "调用已登记能力",
                    "content_scope": "all",
                }
            )

    def test_build_form_has_submit_and_required_fields(self) -> None:
        form = intake.build_requirement_form(
            7, lambda decision: {"decision": decision}
        )
        by_name = {
            element.get("name"): element for element in form["elements"]
        }
        self.assertIn("brand", by_name)
        self.assertIn("activity_query", by_name)
        self.assertEqual(
            by_name["requirement_submit"]["value"]["decision"],
            "pass",
        )
        self.assertEqual(
            by_name["requirement_submit"]["action_type"], "form_submit"
        )
        for name in intake.REQUIRED_FIELDS:
            with self.subTest(name=name):
                self.assertFalse(by_name[name]["required"])

    def test_demo_form_prefills_every_field(self) -> None:
        form = intake.build_requirement_form(
            7,
            lambda decision: {"decision": decision},
            defaults=intake.DEMO_REQUIREMENT_DEFAULTS,
        )
        by_name = {
            element.get("name"): element for element in form["elements"]
        }
        for name in (
            "brand",
            "activity_name",
            "brand_query",
            "brand_filter",
            "activity_query",
            "activity_filter",
            "product_query",
        ):
            with self.subTest(name=name):
                self.assertEqual(
                    by_name[name]["default_value"],
                    intake.DEMO_REQUIREMENT_DEFAULTS[name],
                )
        self.assertEqual(
            by_name["period_start"]["initial_date"], "2026-09-28"
        )
        self.assertEqual(
            by_name["period_end"]["initial_date"], "2026-10-07"
        )
        self.assertEqual(
            by_name["platforms"]["tag"], "select_static"
        )
        self.assertEqual(
            by_name["platforms"]["initial_option"], "xiaohongshu"
        )
        self.assertEqual(
            by_name["content_scope"]["initial_option"], "all"
        )
        normalized = intake.normalize_requirement(
            intake.DEMO_REQUIREMENT_DEFAULTS
        )
        self.assertEqual(normalized["brand_filter"], "")
        self.assertEqual(normalized["platforms"], ["xiaohongshu"])

    def test_full_form_keeps_platform_multi_select(self) -> None:
        form = intake.build_requirement_form(
            7, lambda decision: {"decision": decision}
        )
        platforms = next(
            element for element in form["elements"]
            if element.get("name") == "platforms"
        )
        self.assertEqual(platforms["tag"], "multi_select_static")
        self.assertNotIn("initial_option", platforms)
        self.assertNotIn("initial_options", platforms)

    def test_requirement_snapshot_shows_all_user_fields(self) -> None:
        requirement = intake.normalize_requirement(
            intake.DEMO_REQUIREMENT_DEFAULTS
        )
        requirement["source_file"] = {
            "name": "瑞幸活动明细.xlsx",
            "mount_path": "internal_mount.xlsx",
        }
        snapshot = intake.format_requirement_snapshot(requirement)
        for expected in (
            "瑞幸活动明细.xlsx（已挂载）",
            "瑞幸×暹罗厘普联名活动",
            "2026-09-28 ～ 2026-10-07",
            "小红书",
            "瑞幸+(暹罗厘普|多栋|厘普|比格)",
            "瑞幸+流心芝士拿铁",
            "内容范围",
            "全部",
            "业务问题",
            "活动实际表现如何",
            "交付期望",
            "飞书营销活动复盘报告",
            "允许动作",
            "调用已登记取数/标注/图表/飞书能力",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, snapshot)
        self.assertEqual(snapshot.count("过滤词"), 2)
        self.assertNotIn("internal_mount.xlsx", snapshot)

    def test_xlsx_container_validation(self) -> None:
        with self.assertRaisesRegex(
            intake.IntakeValidationError, "有效的 .xlsx"
        ):
            intake.validate_xlsx_container(b"not-a-zip")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("xl/workbook.xml", "<workbook/>")
        intake.validate_xlsx_container(buf.getvalue())


class ProgressCardTest(unittest.TestCase):
    def _job(self, **kw) -> ps.PipelineJob:
        base = dict(
            job_id="job_x", chat_id="c", thread_id="", user_open_id="u",
            ma_session_id="s", mode="full", project_dir="d", current_phase="clean",
        )
        base.update(kw)
        return ps.PipelineJob(**base)

    def test_running_card(self) -> None:
        c = card.build_progress_card(
            job=self._job(), status=ps.STATUS_RUNNING, elapsed_sec=65,
            tool_lines=["12:00:00 · clean · 清洗完成"],
        )
        self.assertEqual(c["schema"], "2.0")
        self.assertEqual(c["header"]["template"], "blue")
        self.assertIn("运行中", c["header"]["title"]["content"])

    def test_wait_hc_card(self) -> None:
        c = card.build_progress_card(
            job=self._job(current_phase="G3"), status=ps.STATUS_WAIT_HC,
            elapsed_sec=10, tool_lines=[],
        )
        self.assertEqual(c["header"]["template"], "turquoise")
        self.assertIn("G3", c["header"]["title"]["content"])

    def test_done_card_has_open_button(self) -> None:
        c = card.build_progress_card(
            job=self._job(), status=ps.STATUS_DONE, elapsed_sec=200,
            tool_lines=[], online_url="https://feishu/docx/abc",
        )
        self.assertEqual(c["header"]["template"], "green")
        buttons = [e for e in c["body"]["elements"] if e.get("tag") == "button"]
        self.assertEqual(len(buttons), 1)
        self.assertEqual(
            buttons[0]["behaviors"][0]["default_url"], "https://feishu/docx/abc"
        )

    def test_failed_card_shows_error(self) -> None:
        c = card.build_progress_card(
            job=self._job(task_id="task_x"), status=ps.STATUS_FAILED, elapsed_sec=5,
            tool_lines=[], error="boom",
        )
        self.assertEqual(c["header"]["template"], "orange")
        text = "".join(e.get("content", "") for e in c["body"]["elements"])
        self.assertIn("boom", text)
        retry = [
            element
            for element in c["body"]["elements"]
            if any(
                behavior.get("value", {}).get("action") == "retry_task"
                for behavior in element.get("behaviors", [])
            )
        ]
        self.assertEqual(len(retry), 1)

    def test_g1_review_keeps_full_requirement_snapshot(self) -> None:
        requirement = intake.normalize_requirement(
            intake.DEMO_REQUIREMENT_DEFAULTS
        )
        requirement["source_file"] = {
            "name": "瑞幸活动明细.xlsx",
            "mount_path": "hidden.xlsx",
        }
        event = ps.HcEvent(
            id=1,
            job_id="job_x",
            hc_kind="G1",
            payload={},
            user_decision="pass",
            user_note=json.dumps(requirement, ensure_ascii=False),
            operator_label="俞麟",
            resolved_at=1,
        )
        review_lines = card.format_review_lines([event])
        c = card.build_progress_card(
            job=self._job(),
            status=ps.STATUS_RUNNING,
            elapsed_sec=65,
            tool_lines=[],
            review_lines=review_lines,
        )
        text = "\n".join(
            element.get("content", "") for element in c["body"]["elements"]
        )
        self.assertIn("G1 · ✅ 通过 · 俞麟", text)
        self.assertIn("瑞幸活动明细.xlsx（已挂载）", text)
        self.assertIn("活动取数关键词", text)
        self.assertNotIn("hidden.xlsx", text)
    def test_g2_review_keeps_confirmed_plan_snapshot(self) -> None:
        event = ps.HcEvent(
            id=2,
            job_id="job_x",
            hc_kind="G2",
            payload={
                "review_context": {
                    "analysis_plan": {
                        "revision": 1,
                        "plan_bundle_sha256": "abcdef1234567890",
                        "scope": {
                            "brand": "瑞幸",
                            "activities": ["瑞幸联名活动"],
                            "period_start": "2026-09-28",
                            "period_end": "2026-10-07",
                            "platforms": ["xiaohongshu"],
                            "content_scope": "all",
                        },
                        "report_structure": ["品牌传播表现", "总结与建议"],
                        "preflight": {"status": "READY_PENDING_INPUT"},
                    },
                    "execution_plan": {
                        "plan_bundle_sha256": "abcdef1234567890",
                        "stages": [
                            {"name": "数据准备"},
                            {"name": "发布交付"},
                        ],
                    },
                    "selected_modules": {
                        "plan_bundle_sha256": "abcdef1234567890",
                        "core_plus_conditional": [
                            "brand_context",
                            "activity_performance",
                        ],
                    },
                }
            },
            user_decision="pass",
            operator_label="俞麟",
            resolved_at=1,
        )
        text = "\n".join(card.format_review_lines([event]))
        self.assertIn("G2 · ✅ 通过 · 俞麟", text)
        self.assertIn("**已确认方案**", text)
        self.assertIn("瑞幸联名活动", text)
        self.assertIn("品牌传播表现 → 总结与建议", text)
        self.assertIn("品牌背景、活动表现", text)
        self.assertIn("方案通过，待上传活动明细", text)
        self.assertIn("v1 · `abcdef123456`", text)
        self.assertNotIn("hidden.xlsx", text)

    def test_review_limit_preserves_g1(self) -> None:
        lines = ["G1 · snapshot", *[f"G3 · round {i}" for i in range(8)]]
        c = card.build_progress_card(
            job=self._job(),
            status=ps.STATUS_RUNNING,
            elapsed_sec=65,
            tool_lines=[],
            review_lines=lines,
        )
        text = "\n".join(
            element.get("content", "") for element in c["body"]["elements"]
        )
        self.assertIn("G1 · snapshot", text)
        self.assertIn("G3 · round 7", text)
        self.assertNotIn("G3 · round 0", text)

    def test_internal_g3_is_not_rendered_as_human_review(self) -> None:
        event = ps.HcEvent(
            id=1,
            job_id="job_x",
            hc_kind="G3",
            payload={},
            user_decision="reject",
            user_note="Validator P0=5",
            operator_label="系统",
            resolved_at=1,
        )
        self.assertEqual(card.format_review_lines([event]), [])


class ConfigTest(unittest.TestCase):
    def test_load_ok(self) -> None:
        env = {
            "ARK_API_KEY": "ak",
            "FEISHU_APP_ID": "cli_x",
            "FEISHU_APP_SECRET": "sec",
            "TOPIC5_COORDINATOR_AGENT_ID": "agent_1",
            "TOPIC5_ENVIRONMENT_ID": "env_1",
            "AUTHORIZED_OPEN_IDS": "ou_a, ou_b ou_c",
        }
        cfg = config.load_case_config(env)
        self.assertEqual(cfg.coordinator_agent_id, "agent_1")
        self.assertEqual(cfg.authorized_open_ids, ("ou_a", "ou_b", "ou_c"))
        self.assertTrue(cfg.ark_base_url.startswith("https://"))

    def test_missing_required(self) -> None:
        with self.assertRaises(RuntimeError):
            config.load_case_config({"ARK_API_KEY": "ak"})


class UpdateScriptIsolationTest(unittest.TestCase):
    def test_unit_tests_run_without_external_credentials(self) -> None:
        script = (_CASE_DIR / "update_ma.sh").read_text(encoding="utf-8")
        isolation_block = script.split(
            "ISOLATED_TEST_ENV_KEYS=(", 1
        )[1].split("\n)", 1)[0]
        for name in (
            "ARK_API_KEY",
            "OPENAI_API_KEY",
            "DATAHUB_API_KEY",
            "SSJH_API_TOKEN",
            "FEISHU_APP_ID",
            "FEISHU_APP_SECRET",
            "FEISHU_USER_ACCESS_TOKEN",
            "REPORT_COVER_API_KEY",
        ):
            with self.subTest(name=name):
                self.assertIn(name, isolation_block)

        self.assertIn(
            'env "${env_args[@]}" "${PYTHON[@]}" "$@"',
            script,
        )
        self.assertEqual(
            script.count(
                "run_isolated_test_python -m unittest discover "
                "-s tests -p 'test_*.py'"
            ),
            2,
        )
        self.assertNotIn(
            '"${PYTHON[@]}" -m unittest discover',
            script,
        )


class RuntimeResourcePathTest(unittest.TestCase):
    def _runtime_docs(self) -> list[Path]:
        resources = _CASE_DIR / "ma-resources"
        docs = list((resources / "agents").glob("*.md"))
        docs.extend((resources / "memory").rglob("*.md"))
        docs.extend((resources / "skills").glob("*/SKILL.md"))
        return sorted(docs)

    def test_runtime_docs_have_no_legacy_paths(self) -> None:
        forbidden = (
            ".claude/skills/",
            "/tos-",
            "/mnt/memory/$",
            "TOPIC5_DAG_PATH",
            "../../io/contracts/pipeline.v0.2.yaml",
            "references/chart_rules.md",
            "references/design-spec.md",
            "cd skills/",
        )
        for path in self._runtime_docs():
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                with self.subTest(path=path, token=token):
                    self.assertNotIn(token, text)

    def test_explicit_skill_mount_references_resolve(self) -> None:
        resources = _CASE_DIR / "ma-resources"
        source_dirs = {
            "pipeline-orchestrator": resources / "skills/pipeline-orchestrator",
            "ssjh-social-stat": resources / "skills/SSJH-Social-Stat",
            "blueai-datahub-skill": resources / "skills/blueai-datahub-skill",
            "blueai-yuqing-echarts-skill-new":
                resources / "skills/blueai-yuqing-echarts-skill-new",
            "report-cover-skill": resources / "skills/report-cover-skill",
            "topic5-validator": resources / "skills/topic5-validator",
        }
        pattern = re.compile(
            r"/mnt/skills/([a-z0-9-]+)(/[A-Za-z0-9._/-]+)?"
        )
        for doc in self._runtime_docs():
            for skill_name, suffix in pattern.findall(
                doc.read_text(encoding="utf-8")
            ):
                with self.subTest(doc=doc, mount=skill_name, suffix=suffix):
                    self.assertIn(skill_name, source_dirs)
                    relative = suffix.strip("/")
                    if not relative:
                        self.assertTrue(source_dirs[skill_name].is_dir())
                    elif (
                        skill_name == "pipeline-orchestrator"
                        and relative == "contracts/pipeline.v0.2.yaml"
                    ):
                        self.assertTrue(
                            (resources / "io/contracts/pipeline.v0.2.yaml").is_file()
                        )
                    else:
                        self.assertTrue((source_dirs[skill_name] / relative).exists())

    def test_memory_mount_references_resolve(self) -> None:
        memory_root = _CASE_DIR / "ma-resources/memory/topic5"
        for name in ("MEMORY.md", "错误案例库.md", "_版本状态.md"):
            self.assertTrue((memory_root / name).is_file())
        memory_index = (memory_root / "MEMORY.md").read_text(encoding="utf-8")
        self.assertNotIn("TOPIC5_MEMORY_STORE_ID", memory_index)
        self.assertIn("/mnt/memory/topic5/", memory_index)

    def test_pack_script_injects_canonical_dag(self) -> None:
        script = (_CASE_DIR / "tools/pack_skills.sh").read_text(encoding="utf-8")
        self.assertIn(
            'canonical_dag="$ROOT/ma-resources/io/contracts/pipeline.v0.2.yaml"',
            script,
        )
        self.assertIn(
            'cp "$canonical_dag" "$staged_root/contracts/pipeline.v0.2.yaml"',
            script,
        )
        self.assertIn(
            'cmp -s "$canonical_dag" '
            '"$staged_root/contracts/pipeline.v0.2.yaml"',
            script,
        )
        self.assertIn(
            '"$staged_root/runtime_resources/agents/"',
            script,
        )
        self.assertIn(
            '"$staged_root/runtime_resources/environment.json"',
            script,
        )

    def test_packaged_sources_do_not_embed_credentials(self) -> None:
        skill_root = _CASE_DIR / "ma-resources/skills"
        patterns = (
            re.compile(r'DEFAULT_SECRET_ID\s*=\s*["\'][^"\']+'),
            re.compile(r'DEFAULT_SECRET_KEY\s*=\s*["\'][^"\']+'),
        )
        for path in skill_root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for pattern in patterns:
                with self.subTest(path=path, pattern=pattern.pattern):
                    self.assertIsNone(pattern.search(text))


class RunnerFinalResultParsingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        ark_pkg = types.ModuleType("arkagent")
        ark_mod = types.ModuleType("arkagent.ark")
        ark_mod.ArkClient = type("ArkClient", (), {})
        ark_mod.event_error = lambda event: ""
        ark_mod.event_progress = lambda event: ""
        ark_mod.event_requires_action = lambda event: False
        ark_mod.event_text = lambda event: ""
        feishu_mod = types.ModuleType("arkagent.feishu")
        feishu_mod.FeishuSender = type("FeishuSender", (), {})
        feishu_mod.ResourceRef = type("ResourceRef", (), {})
        session_state_mod = types.ModuleType("arkagent.session_state")
        session_state_mod.is_session_resumable = lambda status: False
        session_state_mod.session_status_from_payload = lambda payload: ""
        cls._old_modules = {
            name: sys.modules.get(name)
            for name in (
                "arkagent",
                "arkagent.ark",
                "arkagent.feishu",
                "arkagent.session_state",
            )
        }
        sys.modules["arkagent"] = ark_pkg
        sys.modules["arkagent.ark"] = ark_mod
        sys.modules["arkagent.feishu"] = feishu_mod
        sys.modules["arkagent.session_state"] = session_state_mod
        spec = importlib.util.spec_from_file_location(
            "_topic5_runner_for_test", _CASE_DIR / "topic5_runner.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        cls.runner = module
        cls._module_name = spec.name

    @classmethod
    def tearDownClass(cls) -> None:
        sys.modules.pop(cls._module_name, None)
        for name, module in cls._old_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    @staticmethod
    def _g3_payload(*, p0_count: int = 0) -> dict:
        issues = [
            {
                "id": f"P0-{index}",
                "severity": "P0",
                "title": f"问题 {index}",
                "evidence": f"证据 {index}",
            }
            for index in range(1, p0_count + 1)
        ]
        return {
            "hc": "G3",
            "run_id": "run-1",
            "review_context": {
                "report_ref": {
                    "artifact_id": "report_md@v1",
                    "key": "report_md",
                    "version": 1,
                    "sha256": "report-sha",
                },
                "qc_report": {
                    "validator": "topic5-validator",
                    "has_p0": p0_count > 0,
                    "p0_count": p0_count,
                    "issues": issues,
                    "subject": {
                        "artifact_id": "report_md@v1",
                        "key": "report_md",
                        "version": 1,
                        "sha256": "report-sha",
                    },
                    "execution_source": "validator-session-1",
                    "rules_version": "rules-v1",
                    "validator_version": "validator-v1",
                    "execution_ref": {
                        "task_id": "task-1",
                        "run_id": "run-1",
                        "attempt_id": "attempt-1",
                    },
                    "qc_metadata": {
                        "mode": "independent-agent",
                        "validator_id": "topic5-validator",
                        "input_artifacts": [
                            {"key": key, "sha256": f"{key}-sha"}
                            for key in (
                                "report_md",
                                "requirement_confirmed",
                                "stats_bundle",
                                "chart_urls",
                                "validator_rules",
                            )
                        ],
                    },
                },
            },
        }

    def test_hc_payload_repairs_only_missing_trailing_closer(self) -> None:
        text = (
            '{"hc":"G2","review_context":{"analysis_plan":{"revision":1}},'
            '"status":"awaiting_human"'
        )
        payload = self.runner.extract_hc_payload(text)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["hc"], "G2")
        self.assertEqual(
            payload["review_context"]["analysis_plan"]["revision"], 1
        )

    def test_tool_result_contract_keeps_exact_hc_payload(self) -> None:
        text = (
            'HC_PENDING {"hc_kind":"G2","review_context":'
            '{"selected_modules":{"plan_bundle_sha256":"exact"}}}'
        )
        with patch.object(self.runner, "event_text", return_value=text):
            self.assertEqual(
                self.runner._event_contract_text(
                    {"type": "agent.tool_result"}
                ),
                text,
            )
            self.assertEqual(
                self.runner._event_contract_text(
                    {"type": "agent.message"}
                ),
                "",
            )
        payload = self.runner.extract_hc_payload(text)
        self.assertEqual(payload["hc"], "G2")
        self.assertEqual(
            payload["review_context"]["selected_modules"][
                "plan_bundle_sha256"
            ],
            "exact",
        )

    def test_latest_turn_events_excludes_historical_hc_results(self) -> None:
        events = [
            {"type": "user.message", "content": "start"},
            {"type": "agent.tool_result", "content": "HC_PENDING G1"},
            {"type": "user.message", "content": "G1 pass"},
            {"type": "agent.tool_result", "content": "HC_PENDING G2"},
        ]
        self.assertEqual(
            self.runner._latest_turn_events(events),
            [events[-1]],
        )

    def test_best_hc_payload_prefers_complete_g3_result(self) -> None:
        pending = {
            "hc": "G3",
            "review_context": {"qc_report_path": "/run/qc.json", "qc_report": None},
        }
        complete = {
            "hc": "G3",
            "review_context": {
                "qc_report_path": "/run/qc.json",
                "qc_report": {
                    "validator": "topic5-validator",
                    "p0_count": 0,
                    "subject": {"artifact_id": "report_md@v1"},
                },
            },
        }
        selected = self.runner._best_hc_payload([
            json.dumps(complete),
            "HC_PENDING " + json.dumps(pending),
        ])
        self.assertEqual(selected, complete)

    def test_best_hc_payload_uses_latest_gate_kind(self) -> None:
        stale_g3 = self._g3_payload(p0_count=13)
        current_g4 = {
            "hc": "G4",
            "review_context": {
                "delivery_draft": {
                    "revision_id": 17,
                    "url": "https://example.test/report",
                }
            },
        }
        selected = self.runner._best_hc_payload([
            json.dumps(stale_g3, ensure_ascii=False),
            json.dumps(current_g4, ensure_ascii=False),
        ])
        self.assertEqual(selected, current_g4)

    def test_g3_auto_decision_uses_independent_validator_result(self) -> None:
        decision, note = self.runner._g3_auto_decision(
            self._g3_payload(p0_count=0)
        )
        self.assertEqual(decision, "pass")
        self.assertIn("P0=0", note)

        decision, note = self.runner._g3_auto_decision(
            self._g3_payload(p0_count=2)
        )
        self.assertEqual(decision, "reject")
        self.assertIn("证据 1", note)
        self.assertIn("证据 2", note)

    def test_g3_auto_decision_fails_closed_on_incomplete_contract(self) -> None:
        payload = self._g3_payload(p0_count=0)
        del payload["review_context"]["qc_report"]["execution_source"]
        with self.assertRaisesRegex(ValueError, "独立身份"):
            self.runner._g3_auto_decision(payload)

    def test_handle_idle_auto_resumes_g3_without_human_event_or_card(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ps.PipelineStore(str(Path(temp_dir) / "runner.db"))
            job = store.create_job("c", "", "u", "session-1", "demo", "")
            runner = self.runner.Topic5Runner(
                object(),
                object(),
                store,
                self.runner.Topic5Config("agent", "env"),
            )
            decisions = []

            async def fake_resume(current_job, message):
                decisions.append((current_job.job_id, message))
                store.resume_running(current_job.job_id, current_job.current_phase)

            class RejectCardSender:
                async def send_hc_card(self, *_args, **_kwargs):
                    raise AssertionError("G3 must not render a human card")

            runner.resume_job = fake_resume
            runner._card_sender = RejectCardSender()
            asyncio.run(runner._handle_idle(
                job.job_id,
                [json.dumps(self._g3_payload(p0_count=2))],
                recovering=True,
            ))
            self.assertEqual(len(decisions), 1)
            self.assertEqual(decisions[0][0], job.job_id)
            self.assertIn("G3 reject:", decisions[0][1])
            self.assertEqual(
                store._conn.execute(
                    "SELECT COUNT(*) FROM pipeline_hc_events WHERE job_id = ?",
                    (job.job_id,),
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                [item["receipt_type"] for item in store.list_receipts(job.job_id)],
                ["artifact.ready"],
            )
            self.assertEqual(store.get_job(job.job_id).status, ps.STATUS_RUNNING)
            store.close()

    def test_handle_idle_fails_g3_without_validator_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ps.PipelineStore(str(Path(temp_dir) / "runner.db"))
            job = store.create_job("c", "", "u", "session-1", "demo", "")
            runner = self.runner.Topic5Runner(
                object(),
                object(),
                store,
                self.runner.Topic5Config("agent", "env"),
            )

            async def no_render(*_args, **_kwargs):
                return None

            runner._render_and_patch = no_render
            payload = self._g3_payload(p0_count=0)
            del payload["review_context"]["qc_report"]["qc_metadata"]
            asyncio.run(runner._handle_idle(
                job.job_id, [json.dumps(payload)], recovering=True
            ))
            failed = store.get_job(job.job_id)
            self.assertEqual(failed.status, ps.STATUS_FAILED)
            self.assertIn("Validator 契约校验失败", failed.last_error)
            self.assertEqual(
                store._conn.execute(
                    "SELECT COUNT(*) FROM pipeline_hc_events WHERE job_id = ?",
                    (job.job_id,),
                ).fetchone()[0],
                0,
            )
            store.close()

    def test_hc_payload_does_not_repair_truncated_string(self) -> None:
        text = '{"hc":"G2","prompt":"方案确认'
        self.assertIsNone(self.runner.extract_hc_payload(text))

    def test_hc_payload_does_not_repair_mismatched_brackets(self) -> None:
        text = '{"hc":"G2","expect":[1,2}}'
        self.assertIsNone(self.runner.extract_hc_payload(text))

    def test_rejects_cross_json_terminal_splice(self) -> None:
        text = (
            '{"status":"completed","online_url":"https://open.feishu.cn/docx/a"}\n'
            '{"status":"completed","handoff_validated":true,"degraded":false}'
        )
        self.assertEqual(self.runner.extract_final_envelope(text), {})
        self.assertEqual(self.runner.extract_final_online_url(text), "")

    def test_accepts_url_only_from_same_final_result(self) -> None:
        payload = {
            "status": "completed",
            "online_url": "https://www.feishu.cn/docx/doc1",
            "handoff_validated": True,
            "terminal_accepted": True,
            "degraded": False,
        }
        text = "FINAL_RESULT " + json.dumps(payload, separators=(",", ":"))
        self.assertEqual(self.runner.extract_final_envelope(text), payload)
        self.assertEqual(
            self.runner.extract_final_online_url(text), payload["online_url"]
        )

    def test_rejects_openapi_domain_as_delivery_url(self) -> None:
        payload = {
            "status": "completed",
            "online_url": "https://open.feishu.cn/docx/doc1",
            "handoff_validated": True,
            "terminal_accepted": True,
            "degraded": False,
        }
        text = "FINAL_RESULT " + json.dumps(payload, separators=(",", ":"))
        self.assertEqual(self.runner.extract_final_online_url(text), "")

    def test_task_accepted_is_recorded_only_when_g1_arrives(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ps.PipelineStore(str(Path(temp_dir) / "runner.db"))
            job = store.create_job("c", "", "u", "session-1", "demo", "")
            store.append_receipt(
                receipt_type="message.ack",
                sender="project_agent",
                recipient="topic5_coordinator",
                job_id=job.job_id,
                correlation_id=job.job_id,
                message_id=f"{job.job_id}:message_ack",
            )
            runner = self.runner.Topic5Runner(
                object(),
                object(),
                store,
                self.runner.Topic5Config("agent", "env"),
            )

            async def no_render(*_args, **_kwargs):
                return None

            async def no_reply(*_args, **_kwargs):
                return None

            runner._render_and_patch = no_render
            runner._reply_async_by_job = no_reply
            self.assertEqual(
                [item["receipt_type"] for item in store.list_receipts(job.job_id)],
                ["message.ack"],
            )
            payload = {
                "hc": "G1",
                "node": "n01_hc_requirement",
                "status": "awaiting_human",
                "prompt": "需求确认",
                "expect": ["requirement_confirmed"],
                "run_id": "run-1",
            }
            asyncio.run(runner._handle_idle(
                job.job_id, [json.dumps(payload, ensure_ascii=False)]
            ))
            self.assertEqual(
                [item["receipt_type"] for item in store.list_receipts(job.job_id)],
                ["message.ack", "task.accepted"],
            )
            store.close()

    def test_startup_recovery_timeout_does_not_block_later_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ps.PipelineStore(str(Path(temp_dir) / "recovery.db"))
            first = store.create_job("c", "", "u1", "session-1", "demo", "")
            second = store.create_job("c", "", "u2", "session-2", "demo", "")
            runner = self.runner.Topic5Runner(
                object(), object(), store,
                self.runner.Topic5Config("agent", "env"),
            )
            original_timeout = self.runner.RECOVERY_JOB_TIMEOUT_SEC
            self.runner.RECOVERY_JOB_TIMEOUT_SEC = 0.01

            async def fake_recover(job):
                if job.job_id == first.job_id:
                    await asyncio.sleep(1)
                return "recovered"

            runner.recover_job = fake_recover
            try:
                results = asyncio.run(runner.recover_active_jobs())
            finally:
                self.runner.RECOVERY_JOB_TIMEOUT_SEC = original_timeout
                store.close()
            self.assertEqual(results[first.job_id], "recovery_timeout")
            self.assertEqual(results[second.job_id], "recovered")


class HitlActionParsingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        stub = types.ModuleType("topic5_runner")
        stub.Topic5CardSenderProtocol = type("Topic5CardSenderProtocol", (), {})
        stub.Topic5Runner = type("Topic5Runner", (), {})
        cls._old_runner = sys.modules.get("topic5_runner")
        sys.modules["topic5_runner"] = stub
        spec = importlib.util.spec_from_file_location(
            "_topic5_hitl_for_test", _CASE_DIR / "topic5_hitl.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        cls.hitl = module
        cls._module_name = spec.name

    @classmethod
    def tearDownClass(cls) -> None:
        sys.modules.pop(cls._module_name, None)
        if cls._old_runner is None:
            sys.modules.pop("topic5_runner", None)
        else:
            sys.modules["topic5_runner"] = cls._old_runner

    def test_g1_form_without_note_returns_none(self) -> None:
        action = {
            "action": {
                "form_value": {
                    "brand": "瑞幸",
                    "activity_name": "联名活动",
                }
            }
        }
        self.assertIsNone(self.hitl._extract_action_note(action))

    def test_legacy_input_value_is_trimmed(self) -> None:
        action = {"action": {"input_value": " 需要补充样本 "}}
        self.assertEqual(
            self.hitl._extract_action_note(action),
            "需要补充样本",
        )

    def test_g1_file_reference_is_not_base64_reencoded(self) -> None:
        reference = "@/mnt/session/uploads/topic5_requirement_job_1.json"
        self.assertEqual(
            self.hitl._build_decision_message("G1", "pass", reference),
            f"G1 pass: {reference}",
        )

    def test_g1_submission_without_file_persists_requirement_snapshot(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ps.PipelineStore(str(Path(temp_dir) / "g1-audit.db"))
            job = store.create_job("c", "", "u", "session-1", "demo", "")
            store.mark_wait_hc(job.job_id, "G1")
            event_id = store.append_hc_event(
                job.job_id, "G1", {"hc": "G1"}
            )
            resumed: list[str] = []

            class FakeFeishu:
                @staticmethod
                def resolve_operator_name(
                    _open_id: str, *, chat_id: str | None = None
                ) -> str:
                    return "俞麟"

            class FakeRunner:
                _feishu = FakeFeishu()

                @staticmethod
                async def attach_requirement_payload(
                    _job, _requirement, _event_id
                ) -> str:
                    return (
                        "@/mnt/session/uploads/"
                        "topic5_requirement_job_1.json"
                    )

                @staticmethod
                async def reply_for_job(_job, _text) -> None:
                    return None

                @staticmethod
                async def resume_job(_job, message: str) -> None:
                    resumed.append(message)

            facade = self.hitl.Topic5Hitl(
                self.hitl.Topic5HitlDeps(
                    store=store, runner=FakeRunner()
                )
            )
            action = {
                "action": {
                    "value": {
                        "decision": "pass",
                        "event_id": event_id,
                        "job_id": job.job_id,
                        "hc": "G1",
                    },
                    "form_value": dict(intake.DEMO_REQUIREMENT_DEFAULTS),
                },
                "operator": {"open_id": "ou_test"},
                "chat_id": "c",
            }
            asyncio.run(facade.handle_card_action(action))

            event = store.get_hc_event(event_id)
            snapshot = json.loads(event.user_note)
            self.assertEqual(snapshot["brand"], "瑞幸")
            self.assertNotIn("source_file", snapshot)
            self.assertEqual(
                resumed,
                [
                    "G1 pass: @/mnt/session/uploads/"
                    "topic5_requirement_job_1.json"
                ],
            )
            review = "\n".join(card.format_review_lines([event]))
            self.assertIn("G1 · ✅ 通过 · 俞麟", review)
            self.assertIn("活动取数关键词", review)
            store.close()

    def test_only_demo_g1_card_prefills_requirement_form(self) -> None:
        base = dict(
            job_id="job_x",
            chat_id="c",
            thread_id="",
            user_open_id="u",
            ma_session_id="s",
            project_dir="d",
        )
        demo = ps.PipelineJob(mode="demo", **base)
        full = ps.PipelineJob(mode="full", **base)
        demo_card = self.hitl.build_hc_card(demo, "G1", 7, {})
        full_card = self.hitl.build_hc_card(full, "G1", 7, {})
        demo_form = next(
            item for item in demo_card["body"]["elements"]
            if item.get("tag") == "form"
        )
        full_form = next(
            item for item in full_card["body"]["elements"]
            if item.get("tag") == "form"
        )
        demo_brand = next(
            item for item in demo_form["elements"]
            if item.get("name") == "brand"
        )
        full_brand = next(
            item for item in full_form["elements"]
            if item.get("name") == "brand"
        )
        self.assertEqual(demo_brand["default_value"], "瑞幸")
        self.assertNotIn("default_value", full_brand)

    def test_g1_submit_is_independent_of_file_attach(self) -> None:
        base = dict(
            job_id="job_x",
            chat_id="c",
            thread_id="",
            user_open_id="u",
            ma_session_id="s",
            mode="demo",
            project_dir="d",
        )
        waiting = ps.PipelineJob(**base)
        attached = ps.PipelineJob(
            **base,
            source_file_id="file_123",
            source_file_name="activity.xlsx",
            source_file_mount_path="activity.xlsx",
        )

        def submit_value(job: ps.PipelineJob) -> dict:
            card_json = self.hitl.build_hc_card(job, "G1", 7, {})
            form = next(
                item for item in card_json["body"]["elements"]
                if item.get("tag") == "form"
            )
            submit = next(
                item for item in form["elements"]
                if item.get("name") == "requirement_submit"
            )
            return submit["value"]

        self.assertEqual(submit_value(waiting)["form_revision"], "requirement")
        self.assertEqual(submit_value(attached)["form_revision"], "requirement")
        self.assertEqual(
            self.hitl._extract_action_value(
                {"action": {"value": submit_value(attached)}}
            )["decision"],
            "pass",
        )

    def test_g2_pass_accepts_consistent_bundle_hash_without_forwarded_refs(self) -> None:
        payload = {
            "review_context": {
                "analysis_plan": {"plan_bundle_sha256": "hash-1"},
                "execution_plan": {
                    "plan_bundle_sha256": "hash-1",
                    "preflight": {"status": "READY"},
                },
                "selected_modules": {"plan_bundle_sha256": "hash-1"},
            },
        }
        self.assertEqual(
            self.hitl._decision_block_reason("G2", payload, "pass"),
            "",
        )

    def test_g2_pass_accepts_plan_pending_activity_input(self) -> None:
        payload = {
            "review_context": {
                "analysis_plan": {"plan_bundle_sha256": "hash-1"},
                "execution_plan": {
                    "plan_bundle_sha256": "hash-1",
                    "preflight": {"status": "READY_PENDING_INPUT"},
                },
                "selected_modules": {"plan_bundle_sha256": "hash-1"},
            },
        }
        self.assertEqual(
            self.hitl._decision_block_reason("G2", payload, "pass"),
            "",
        )

    def test_g2_pass_accepts_forwarded_preflight_under_analysis_plan(self) -> None:
        payload = {
            "review_context": {
                "analysis_plan": {
                    "plan_bundle_sha256": "hash-1",
                    "preflight": {"status": "READY_PENDING_INPUT"},
                },
                "execution_plan": {"plan_bundle_sha256": "hash-1"},
                "selected_modules": {"plan_bundle_sha256": "hash-1"},
            },
        }
        self.assertEqual(
            self.hitl._decision_block_reason("G2", payload, "pass"),
            "",
        )

    def test_g2_pass_waits_for_file_before_resuming(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ps.PipelineStore(str(Path(temp_dir) / "g2-input.db"))
            job = store.create_job("c", "", "u", "session-1", "demo", "")
            store.mark_wait_hc(job.job_id, "G2")
            payload = {
                "hc": "G2",
                "review_context": {
                    "analysis_plan": {"plan_bundle_sha256": "hash-1"},
                    "execution_plan": {
                        "plan_bundle_sha256": "hash-1",
                        "preflight": {"status": "READY_PENDING_INPUT"},
                    },
                    "selected_modules": {"plan_bundle_sha256": "hash-1"},
                },
            }
            event_id = store.append_hc_event(job.job_id, "G2", payload)
            calls: list[str] = []

            class FakeFeishu:
                @staticmethod
                def resolve_operator_name(
                    _open_id: str, *, chat_id: str | None = None
                ) -> str:
                    return "俞麟"

            class FakeRunner:
                _feishu = FakeFeishu()

                @staticmethod
                async def reply_for_job(_job, _text) -> None:
                    return None

                @staticmethod
                async def patch_job_card(_job_id, _card) -> str:
                    return "om_card"

                @staticmethod
                async def wait_for_activity_file(_job) -> None:
                    calls.append("wait_input")

                @staticmethod
                async def resume_job(_job, _message) -> None:
                    calls.append("resume")

            facade = self.hitl.Topic5Hitl(
                self.hitl.Topic5HitlDeps(store=store, runner=FakeRunner())
            )
            action = {
                "action": {
                    "value": {
                        "decision": "pass",
                        "event_id": event_id,
                        "job_id": job.job_id,
                        "hc": "G2",
                    }
                },
                "operator": {"open_id": "ou_test"},
                "chat_id": "c",
            }
            asyncio.run(facade.handle_card_action(action))
            self.assertEqual(calls, ["wait_input"])
            self.assertEqual(store.get_hc_event(event_id).user_decision, "pass")
            store.close()

    def test_g2_pass_rejects_inconsistent_bundle_hash(self) -> None:
        payload = {
            "review_context": {
                "analysis_plan": {"plan_bundle_sha256": "hash-1"},
                "execution_plan": {
                    "plan_bundle_sha256": "hash-2",
                    "preflight": {"status": "READY"},
                },
                "selected_modules": {"plan_bundle_sha256": "hash-1"},
            },
        }
        self.assertIn(
            "版本哈希不一致",
            self.hitl._decision_block_reason("G2", payload, "pass"),
        )

    def test_requirement_form_is_shown_without_file(self) -> None:
        job = ps.PipelineJob(
            job_id="job_x",
            chat_id="c",
            thread_id="",
            user_open_id="u",
            ma_session_id="s",
            mode="demo",
            project_dir="d",
        )
        card = self.hitl.build_hc_card(job, "G1", 7, {"hc": "G1"})
        submit = next(
            element
            for block in card["body"]["elements"]
            if block.get("tag") == "form"
            for element in block["elements"]
            if element.get("name") == "requirement_submit"
        )
        self.assertNotIn("disabled", submit)

    def test_g1_resolved_card_contains_requirement_snapshot(self) -> None:
        requirement = intake.normalize_requirement(
            intake.DEMO_REQUIREMENT_DEFAULTS
        )
        requirement["source_file"] = {
            "name": "瑞幸活动明细.xlsx",
            "mount_path": "hidden.xlsx",
        }
        job = ps.PipelineJob(
            job_id="job_x",
            chat_id="c",
            thread_id="",
            user_open_id="u",
            ma_session_id="s",
            mode="demo",
            project_dir="d",
        )
        resolved = self.hitl.build_hc_resolved_card(
            job,
            "G1",
            {},
            "pass",
            json.dumps(requirement, ensure_ascii=False),
            "俞麟",
        )
        text = "\n".join(
            element.get("content", "")
            for element in resolved["body"]["elements"]
        )
        self.assertIn("G1 需求快照", text)
        self.assertIn("瑞幸活动明细.xlsx（已挂载）", text)
        self.assertIn("产品关键词", text)
        self.assertNotIn("hidden.xlsx", text)

    def test_later_hc_card_keeps_g1_review_snapshot(self) -> None:
        card_json = self.hitl.build_hc_card(
            ps.PipelineJob(
                job_id="job_x",
                chat_id="c",
                thread_id="",
                user_open_id="u",
                ma_session_id="s",
                mode="demo",
                project_dir="d",
            ),
            "G2",
            8,
            {"prompt": "确认方案"},
            review_lines=(
                "G1 · ✅ 通过 · 俞麟 · 01:11\n"
                "  - **活动明细文件**：瑞幸活动明细.xlsx（已挂载）",
            ),
        )
        text = "\n".join(
            element.get("content", "")
            for element in card_json["body"]["elements"]
        )
        self.assertIn("已完成审核", text)
        self.assertIn("瑞幸活动明细.xlsx（已挂载）", text)
        self.assertIn("确认方案", text)

    def test_g2_card_renders_reviewable_plan_instead_of_raw_json(self) -> None:
        payload = {
            "prompt": "方案确认（G2）",
            "review_context": {
                "analysis_plan": {
                    "scope": {
                        "brand": "瑞幸",
                        "activities": ["瑞幸×暹罗厘普联名活动"],
                        "period_start": "2026-09-28",
                        "period_end": "2026-10-07",
                        "platforms": ["xiaohongshu"],
                        "content_scope": "all",
                        "source_file_name": "瑞幸活动明细.xlsx",
                    },
                    "metrics": [
                        {
                            "name": "NSR",
                            "definition": "(正面内容数 - 负面内容数) / 正中负内容总数",
                            "neutral_in_denominator": True,
                        }
                    ],
                    "report_structure": ["品牌传播表现", "活动传播表现", "总结与建议"],
                    "guardrails": ["竞品分析已下线，不生成竞品章节"],
                    "revision": 2,
                    "data_requirements": [
                        {
                            "name": "activity_detail",
                            "authority": "user-uploaded-excel",
                            "validation": "XLSX container and schema",
                        },
                        {
                            "name": "brand_period_and_baseline",
                            "authority": "controlled-stat-tool",
                        },
                    ],
                    "chart_plan": [
                        {
                            "section": "品牌传播表现",
                            "chart": "平台声量分布",
                            "type": "donut",
                            "condition": "more than one platform",
                        },
                    ],
                },
                "execution_plan": {
                    "stages": [
                        {"name": "数据准备", "work": "校验上传明细并拉取数据"},
                        {"name": "发布交付", "work": "发布飞书文档并回读"},
                    ],
                    "human_gates": [{"gate": "G3", "purpose": "确认报告质检结果"}],
                    "delivery": "飞书文档，绑定报告版本与 revision_id",
                    "modules": [{
                        "module": "brand_context",
                        "route": "SSJH-Social-Stat",
                        "inputs": ["confirmed_plan.brand_query"],
                        "outputs": ["brand_stats"],
                        "failure_strategy": "fail-closed",
                    }],
                    "preflight": {
                        "status": "READY",
                        "checks": [
                            {
                                "name": "activity_detail_reference",
                                "ok": True,
                                "detail": "瑞幸活动明细.xlsx",
                            },
                        ],
                    },
                },
                "selected_modules": {
                    "core_plus_conditional": ["brand_context", "baseline_lift"],
                    "reasons": {"baseline_lift": "对比活动期与前置基准期"},
                },
                "artifact_refs": {
                    "analysis_plan": {
                        "artifact_id": "analysis_plan@v1",
                        "sha256": "secret-internal-hash",
                    },
                },
            },
        }
        card_json = self.hitl.build_hc_card(
            ps.PipelineJob(
                job_id="job_x",
                chat_id="c",
                thread_id="",
                user_open_id="u",
                ma_session_id="s",
                mode="demo",
                project_dir="d",
            ),
            "G2",
            8,
            payload,
        )
        text = "\n".join(
            element.get("content", "")
            for element in card_json["body"]["elements"]
        )
        for expected in (
            "待确认方案 · 复盘范围",
            "瑞幸×暹罗厘普联名活动",
            "瑞幸活动明细.xlsx",
            "NSR",
            "分母含中性",
            "执行步骤",
            "基准期对比",
            "竞品分析已下线",
            "用户上传的活动明细文件",
            "已登记的社媒统计能力",
            "环形图",
            "检查通过，可以开始执行",
            "确认后将冻结方案版本 v2",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, text)
        self.assertNotIn('"review_context"', text)
        for internal in (
            "user-uploaded-excel",
            "controlled-stat-tool",
            "SSJH-Social-Stat",
            "confirmed_plan.brand_query",
            "brand_stats",
            "fail-closed",
            "secret-internal-hash",
            "逐模块执行契约",
            "SHA-256",
        ):
            with self.subTest(internal=internal):
                self.assertNotIn(internal, text)

        reject_form = next(
            item for item in card_json["body"]["elements"]
            if item.get("name") == "reject_form_8"
        )
        reject = next(
            item for item in reject_form["elements"]
            if item.get("name") == "reject_submit"
        )
        self.assertEqual(reject["value"]["decision"], "reject")


if __name__ == "__main__":
    unittest.main()
