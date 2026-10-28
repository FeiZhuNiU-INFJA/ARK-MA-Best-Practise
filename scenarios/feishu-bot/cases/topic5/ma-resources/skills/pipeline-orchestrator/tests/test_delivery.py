"""波次6：飞书交付闭环 + Validator 契约测试。

无飞书凭据时 delivery_draft 走确定性降级（feishu.fallback，degraded=True），验证：
  - delivery_draft 产 delivery_draft/external_receipts/feishu_doc（schema 一致、含 revision_id）；
  - 幂等复用：已有 external_receipts.doc_token 则复用、不另造新 token；
  - delivery_commit 绑定 revision_id/report_version（版本链对齐）、发布后检查；
  - handoff 复读 revision（降级时以绑定 revision 为准）、校验版本链、写 handoff.json；
  - revision 被改动 → handoff.validated=False（作废告警）；
  - Validator 全量扫描：禁用措辞/多活动相加/缺章 → P0；干净报告 → has_p0=False 放行。
"""
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import tempfile
import shutil
import unittest
from unittest.mock import patch

import feishu
import handlers
import validator
from _util import make_state


def _node(node_id, outputs):
    return {"id": node_id, "name": node_id, "outputs": outputs}


_REPORT_MD = (
    "# 立白营销活动复盘报告\n\n"
    "## 品牌传播表现\n"
    "### 1.1 传播概况\n品牌提及率 0.8，条均互动量 12.5。\n"
    "### 1.2 口碑分析\nNSR 0.3。\n"
    "### 1.3 内容分析\n平台和内容类型分布。\n"
    "### 1.4 热门内容\n引用高互动帖子。\n\n"
    "## 活动传播表现\n"
    "### 2.1 活动 测试活动\n"
    "#### 2.1.1 传播概况\n活动相关内容 100 条。\n"
    "#### 2.1.2 活动声量渗透分析\n产品提及占比 0.5。\n"
    "#### 2.1.3 口碑分析\nNSR 0.3。\n"
    "#### 2.1.4 内容分析\nUGC 与 PGC 分布。\n"
    "#### 2.1.5 热门内容\n引用代表帖子。\n\n"
    "## 综合定位\n![封面](https://cdn.example/cover.png)\n\n"
    "## 经验总结\n证据充分，逐活动统计。\n"
)


def _clear_feishu_creds():
    for k in ("FEISHU_USER_ACCESS_TOKEN", "FEISHU_APP_ID", "FEISHU_APP_SECRET",
              "FEISHU_USER_OPEN_ID"):
        os.environ.pop(k, None)


class TestFeishuAdapter(unittest.TestCase):
    def setUp(self):
        _clear_feishu_creds()

    def test_unavailable_without_creds(self):
        self.assertFalse(feishu.api_available())

    def test_md_to_blocks_headings_and_images(self):
        blocks = feishu.md_to_blocks(_REPORT_MD)
        types = {b["block_type"] for b in blocks}
        self.assertIn(3, types)   # heading1（整数枚举）
        self.assertIn(4, types)   # heading2
        # 外链图片（非本地文件）降级为 text block 承载 URL（可追溯）。
        self.assertTrue(any("cover.png" in b.get("text", {}).get(
            "elements", [{}])[0].get("text_run", {}).get("content", "")
            for b in blocks if b["block_type"] == 2))

    def test_md_to_blocks_local_image_becomes_image_block(self):
        """本地 PNG 路径的图片 → 空 image block（block_type=27）+ 内部 _local_path 提示。"""
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"\x89PNG\r\n\x1a\n fake png bytes")
            png = f.name
        try:
            md = f"# 标题\n\n![情感图]({png})\n\n正文。\n"
            blocks = feishu.md_to_blocks(md)
            img = [b for b in blocks if b["block_type"] == 27]
            self.assertEqual(len(img), 1)
            self.assertEqual(img[0]["_local_path"], png)
            self.assertEqual(img[0]["_alt"], "情感图")
            # 剥离内部键后不含下划线前缀键。
            sent = feishu._strip_internal(img[0])
            self.assertNotIn("_local_path", sent)
            self.assertEqual(sent["block_type"], 27)
        finally:
            os.unlink(png)

    def test_md_to_blocks_native_table(self):
        md = (
            "# 标题\n\n"
            "| 指标 | 数值 | 备注 |\n"
            "|:---|---:|:---:|\n"
            "| 声量 | 42 | A\\|B |\n\n"
            "正文。\n"
        )
        blocks = feishu.md_to_blocks(md)
        self.assertEqual([b["block_type"] for b in blocks], [3, 31, 2])
        table = blocks[1]
        self.assertEqual(table["table"]["property"], {
            "row_size": 2,
            "column_size": 3,
            "header_row": True,
        })
        self.assertEqual(table["_table_rows"], [
            ["指标", "数值", "备注"],
            ["声量", "42", "A|B"],
        ])
        self.assertNotIn("_table_rows", feishu._strip_internal(table))

    def test_fill_tables_writes_each_native_cell(self):
        submitted = feishu.md_to_blocks(
            "| 指标 | 数值 |\n|---|---:|\n| 声量 | 42 |\n")
        created = [{
            "block_id": "table_1",
            "block_type": 31,
            "table": {"cells": ["c1", "c2", "c3", "c4"]},
        }]
        calls = []

        def fake_req(method, path, token, body=None, timeout=30):
            calls.append((method, path, body))
            return {}

        with patch("feishu._req", side_effect=fake_req):
            filled = feishu._fill_tables("token", "doc", submitted, created)

        self.assertEqual(filled, 1)
        self.assertEqual(len(calls), 4)
        self.assertEqual([
            call[2]["children"][0]["text"]["elements"][0]["text_run"]["content"]
            for call in calls
        ], ["指标", "数值", "声量", "42"])

    def test_fallback_deterministic_token(self):
        a = feishu.fallback(_REPORT_MD, title="t", task_id="t5", report_version="0.0")
        b = feishu.fallback(_REPORT_MD, title="t", task_id="t5", report_version="0.0")
        self.assertEqual(a["doc_token"], b["doc_token"])
        self.assertEqual(
            a["url"], f"https://www.feishu.cn/docx/{a['doc_token']}"
        )
        self.assertTrue(a["degraded"])
        self.assertTrue(a["readback"]["ok"])

    def test_current_revision_none_without_creds(self):
        self.assertIsNone(feishu.current_revision("docx_stub_x"))

    def test_list_document_blocks_follows_all_pages(self):
        calls = []

        def fake_req(method, path, token, body=None, timeout=30):
            calls.append(path)
            if "page_token=" in path:
                return {"items": [{"block_id": "b2"}], "has_more": False}
            return {
                "items": [{"block_id": "b1"}],
                "has_more": True,
                "page_token": "next/+ token",
            }

        with patch("feishu._req", side_effect=fake_req):
            blocks = feishu._list_document_blocks("token", "doc")

        self.assertEqual([block["block_id"] for block in blocks], ["b1", "b2"])
        self.assertIn("page_token=next%2F%2B%20token", calls[1])

    def test_delivery_owner_prefers_task_initiator(self):
        seen = []

        def fake_req(method, path, token, body=None, timeout=30):
            if method == "POST" and path.endswith("/documents"):
                return {"document": {"document_id": "doc_owner"}}
            if "transfer_owner" in path:
                seen.append(body["member_id"])
            if method == "GET" and path.endswith("/documents/doc_owner"):
                return {"document": {"revision_id": 1}}
            if method == "GET" and "/blocks" in path:
                return {"items": []}
            return {}

        with patch.dict(os.environ, {
            "FEISHU_USER_OPEN_ID": "ou_initiator",
        }, clear=False), \
                patch("feishu._tenant_token", return_value="t-token"), \
                patch("feishu._req", side_effect=fake_req):
            result = feishu.create_delivery(
                "", title="测试", task_id="t5", report_version="0.0"
            )

        self.assertEqual(seen, ["ou_initiator"])
        self.assertTrue(result["ownership_transferred"])

    def test_delivery_preserves_owner_transfer_error(self):
        calls = []

        def fake_req(method, path, token, body=None, timeout=30):
            if method == "POST" and path.endswith("/documents"):
                return {"document": {"document_id": "doc_owner"}}
            if "transfer_owner" in path:
                raise RuntimeError("permission denied")
            if path.endswith("/members?type=docx&need_notification=false"):
                calls.append(body)
                return {"member": body}
            if method == "GET" and path.endswith("/documents/doc_owner"):
                return {"document": {"revision_id": 1}}
            if method == "GET" and "/blocks" in path:
                return {"items": []}
            return {}

        with patch.dict(
            os.environ, {"FEISHU_USER_OPEN_ID": "ou_initiator"}, clear=False
        ), patch("feishu._tenant_token", return_value="t-token"), patch(
            "feishu._req", side_effect=fake_req
        ):
            result = feishu.create_delivery(
                "", title="测试", task_id="t5", report_version="0.0"
            )

        self.assertFalse(result["ownership_transferred"])
        self.assertEqual(result["ownership_target"], "ou_initiator")
        self.assertIn("permission denied", result["ownership_error"])
        self.assertTrue(result["collaborator_added"])
        self.assertEqual(result["collaborator_perm"], "full_access")
        self.assertTrue(result["delivery_access_granted"])
        self.assertEqual(calls, [{
            "member_type": "openid",
            "member_id": "ou_initiator",
            "perm": "full_access",
            "type": "user",
        }])

    def test_delivery_preserves_both_owner_and_collaborator_errors(self):
        def fake_req(method, path, token, body=None, timeout=30):
            if method == "POST" and path.endswith("/documents"):
                return {"document": {"document_id": "doc_owner"}}
            if "transfer_owner" in path:
                raise RuntimeError("transfer permission denied")
            if path.endswith("/members?type=docx&need_notification=false"):
                raise RuntimeError("collaborator permission denied")
            if method == "GET" and path.endswith("/documents/doc_owner"):
                return {"document": {"revision_id": 1}}
            if method == "GET" and "/blocks" in path:
                return {"items": []}
            return {}

        with patch.dict(
            os.environ, {"FEISHU_USER_OPEN_ID": "ou_initiator"}, clear=False
        ), patch("feishu._tenant_token", return_value="t-token"), patch(
            "feishu._req", side_effect=fake_req
        ):
            result = feishu.create_delivery(
                "", title="测试", task_id="t5", report_version="0.0"
            )

        self.assertFalse(result["ownership_transferred"])
        self.assertFalse(result["collaborator_added"])
        self.assertFalse(result["delivery_access_granted"])
        self.assertIn("transfer permission denied", result["ownership_error"])
        self.assertIn(
            "collaborator permission denied", result["collaborator_error"]
        )

    def test_create_delivery_uploads_media_and_fills_image(self):
        """真路径（monkeypatch HTTP）：本地图 → 建 image block → 上传 media → PATCH 回填。

        核对 create_delivery 对每张本地 PNG 调 _upload_media 并 PATCH replace_image，
        且 readback 统计到的 image block 数 == 期望本地图数。
        """
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"\x89PNG\r\n\x1a\n fake")
            png = f.name
        md = f"# 立白复盘\n\n## 活动传播\n![情感图]({png})\n\n## 经验总结\n证据充分。\n"
        calls = {"upload": 0, "patch": 0}

        def fake_tenant_token():
            return "t-token"

        def fake_upload_media(token, local_path, parent_node, timeout=60):
            calls["upload"] += 1
            self.assertEqual(local_path, png)
            self.assertTrue(parent_node)
            return "media_token_123"

        def fake_req(method, path, token, body=None, timeout=30):
            if method == "POST" and path.endswith("/documents"):
                return {"document": {"document_id": "doc_abc"}}
            if method == "POST" and "/children" in path:
                # 按提交顺序回传同数量 block，image block 带 block_id。
                children = []
                for b in body["children"]:
                    bid = f"blk_{len(children)}"
                    children.append({"block_id": bid, "block_type": b["block_type"]})
                return {"children": children}
            if method == "PATCH":
                calls["patch"] += 1
                return {}
            if method == "GET" and path.endswith("/documents/doc_abc"):
                return {"document": {"revision_id": 7}}
            if method == "GET" and "/blocks" in path:
                # 回读：返回两个 heading、一个 image block。
                return {"items": [
                    {"block_type": 3, "heading1": {"elements": [
                        {"text_run": {"content": "立白复盘"}}]}},
                    {"block_type": 4, "heading2": {"elements": [
                        {"text_run": {"content": "活动传播"}}]}},
                    {"block_type": 4, "heading2": {"elements": [
                        {"text_run": {"content": "经验总结"}}]}},
                    {"block_type": 27, "image": {}},
                ]}
            return {}

        orig = (feishu._tenant_token, feishu._upload_media, feishu._req)
        feishu._tenant_token, feishu._upload_media, feishu._req = (
            fake_tenant_token, fake_upload_media, fake_req)
        try:
            res = feishu.create_delivery(md, title="立白复盘", task_id="t5",
                                         report_version="0.0")
        finally:
            os.unlink(png)
            feishu._tenant_token, feishu._upload_media, feishu._req = orig

        self.assertFalse(res["degraded"])
        self.assertEqual(res["doc_token"], "doc_abc")
        self.assertEqual(res["url"], "https://www.feishu.cn/docx/doc_abc")
        self.assertEqual(res["revision_id"], 7)
        self.assertEqual(res["images_filled"], 1)
        self.assertEqual(calls["upload"], 1)
        self.assertEqual(calls["patch"], 1)
        self.assertTrue(res["readback"]["images_referenced"])
        self.assertTrue(res["readback"]["ok"])


class TestDeliveryHandlers(unittest.TestCase):
    def setUp(self):
        _clear_feishu_creds()
        self.ws = tempfile.mkdtemp()
        self.st = make_state(self.ws, task_id="t5")
        self.st.write_artifact("report_md", {
            "markdown": _REPORT_MD, "chapters": "five",
            "version": "0.0", "source": "fallback", "degraded": True})
        self.st.write_artifact("chart_urls", {"charts": [], "degraded": True})
        self.st.write_artifact("confirmed_plan", {"brand": "立白"})
        self.st.write_artifact("qc_report", {
            "has_p0": False, "p0_count": 0, "issues": [],
        })

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    def test_delivery_draft_rejects_missing_credentials(self):
        with self.assertRaisesRegex(RuntimeError, "凭据不可用"):
            handlers.get("delivery_draft")(
                _node("n20_delivery_draft",
                      ["delivery_draft", "external_receipts", "feishu_doc"]),
                self.st,
                {},
            )

    def test_delivery_draft_idempotent_reuse(self):
        """已有 external_receipts.doc_token 则复用同一 token（幂等复发不换文档）。"""
        created = {
            "doc_token": "doc_real",
            "url": "https://www.feishu.cn/docx/doc_real",
            "revision_id": 7,
            "report_version": "0.0",
            "ownership_transferred": True,
            "readback": {"ok": True, "revision_id": 7},
            "degraded": False,
        }
        with patch("handlers.feishu.api_available", return_value=True), \
                patch("handlers.feishu.create_delivery", return_value=created):
            first = handlers.get("delivery_draft")(
                _node("n20_delivery_draft",
                      ["delivery_draft", "external_receipts", "feishu_doc"]),
                self.st,
                {},
            )
        tok = first["artifacts"]["external_receipts"]["doc_token"]
        self.st.write_artifact("external_receipts", first["artifacts"]["external_receipts"])
        with patch("handlers.feishu.api_available", return_value=True), \
                patch("handlers.feishu.create_delivery", return_value=created) as create:
            second = handlers.get("delivery_draft")(
                _node("n20_delivery_draft",
                      ["delivery_draft", "external_receipts", "feishu_doc"]),
                self.st,
                {},
            )
        self.assertEqual(second["artifacts"]["external_receipts"]["doc_token"], tok)
        self.assertEqual(create.call_args.kwargs["reused_doc_token"], tok)

    def test_delivery_draft_merges_post_publish_p0_into_qc_report(self):
        self.st.write_artifact("qc_report", {
            "has_p0": False,
            "p0_count": 0,
            "issues": [{
                "id": "P1-existing",
                "severity": "P1",
                "title": "已有披露项",
            }, {
                "id": "P0-postpublish-chapters",
                "severity": "P0",
                "title": "旧的同 ID 发布后问题",
            }],
        })
        created = {
            "doc_token": "doc_real",
            "url": "https://www.feishu.cn/docx/doc_real",
            "revision_id": 8,
            "report_version": "0.0",
            "ownership_transferred": False,
            "readback": {
                "ok": False,
                "revision_id": 8,
                "chapters_complete": False,
                "missing_chapters": ["04 经验总结"],
                "images_referenced": False,
                "expected_images": 3,
                "got_images": 2,
            },
            "degraded": False,
        }
        with patch("handlers.feishu.api_available", return_value=True), \
                patch("handlers.feishu.create_delivery", return_value=created):
            result = handlers.get("delivery_draft")(
                _node("n20_delivery_draft", [
                    "delivery_draft", "external_receipts", "feishu_doc",
                    "qc_report",
                ]),
                self.st,
                {},
            )

        qc = result["artifacts"]["qc_report"]
        issue_ids = [issue["id"] for issue in qc["issues"]]
        self.assertEqual(qc["p0_count"], 3)
        self.assertTrue(qc["has_p0"])
        self.assertEqual(len(issue_ids), len(set(issue_ids)))
        self.assertIn("P1-existing", issue_ids)
        self.assertIn("P0-postpublish-chapters", issue_ids)
        self.assertIn("P0-postpublish-images", issue_ids)
        self.assertIn("P0-postpublish-owner", issue_ids)

    def test_delivery_gate_routes_p0_to_fix_before_g4(self):
        self.st.write_artifact("delivery_draft", {
            "state": "needs_review",
            "post_publish_validation": {
                "has_p0": True,
                "p0_count": 1,
                "issues": [{
                    "id": "P0-postpublish-images",
                    "severity": "P0",
                    "locus": "feishu-docx",
                }],
            },
        })
        route = handlers.get("delivery_draft_decision")(
            _node("n20_delivery_gate", []), self.st, {}
        )
        self.assertEqual(route, {"route": "has_p0_retry"})

    def test_delivery_draft_accepts_full_access_collaborator_fallback(self):
        created = {
            "doc_token": "doc_real",
            "url": "https://www.feishu.cn/docx/doc_real",
            "revision_id": 8,
            "report_version": "0.0",
            "ownership_transferred": False,
            "ownership_target": "ou_initiator",
            "ownership_error": "RuntimeError: Permission denied",
            "collaborator_added": True,
            "collaborator_perm": "full_access",
            "collaborator_error": "",
            "delivery_access_granted": True,
            "readback": {
                "ok": True,
                "revision_id": 8,
                "chapters_complete": True,
                "missing_chapters": [],
                "images_referenced": True,
                "expected_images": 13,
                "got_images": 13,
            },
            "degraded": False,
        }
        with patch("handlers.feishu.api_available", return_value=True), patch(
            "handlers.feishu.create_delivery", return_value=created
        ):
            result = handlers.get("delivery_draft")(
                _node("n20_delivery_draft", [
                    "delivery_draft", "external_receipts", "feishu_doc",
                    "qc_report",
                ]),
                self.st,
                {},
            )

        draft = result["artifacts"]["delivery_draft"]
        qc = result["artifacts"]["qc_report"]
        self.assertEqual(draft["state"], "ready")
        self.assertFalse(draft["ownership_transferred"])
        self.assertTrue(draft["collaborator_added"])
        self.assertTrue(draft["delivery_access_granted"])
        self.assertFalse(qc["has_p0"])
        self.assertEqual(qc["p0_count"], 0)
        self.assertIn(
            "P1-postpublish-owner-fallback",
            [issue["id"] for issue in qc["issues"]],
        )

    def test_delivery_retry_replaces_stale_post_publish_permission_p0(self):
        self.st.write_artifact("qc_report", {
            "has_p0": True,
            "p0_count": 1,
            "issues": [{
                "id": "P0-postpublish-owner",
                "severity": "P0",
                "locus": "feishu-permission",
            }],
        })
        created = {
            "doc_token": "doc_real",
            "url": "https://www.feishu.cn/docx/doc_real",
            "revision_id": 9,
            "report_version": "0.0",
            "ownership_transferred": True,
            "ownership_target": "ou_initiator",
            "ownership_error": "",
            "collaborator_added": False,
            "collaborator_perm": "",
            "collaborator_error": "",
            "delivery_access_granted": True,
            "readback": {
                "ok": True,
                "revision_id": 9,
                "chapters_complete": True,
                "missing_chapters": [],
                "images_referenced": True,
                "expected_images": 13,
                "got_images": 13,
            },
            "degraded": False,
        }
        with patch("handlers.feishu.api_available", return_value=True), patch(
            "handlers.feishu.create_delivery", return_value=created
        ):
            result = handlers.get("delivery_draft")(
                _node("n20_delivery_draft", [
                    "delivery_draft", "external_receipts", "feishu_doc",
                    "qc_report",
                ]),
                self.st,
                {},
            )

        qc = result["artifacts"]["qc_report"]
        self.assertFalse(qc["has_p0"])
        self.assertNotIn(
            "P0-postpublish-owner",
            [issue["id"] for issue in qc["issues"]],
        )

    def test_delivery_gate_routes_ownership_failure_directly_to_hc7(self):
        self.st.write_artifact("delivery_draft", {
            "state": "needs_review",
            "post_publish_validation": {
                "has_p0": True,
                "p0_count": 1,
                "issues": [{
                    "id": "P0-postpublish-owner",
                    "severity": "P0",
                    "title": "飞书文档未完成所有权转移",
                    "evidence": (
                        "ownership_transferred=false; 飞书 HTTP 403 "
                        "code=1063002 msg=Permission denied"
                    ),
                    "locus": "feishu-permission",
                }],
            },
        })
        route = handlers.get("delivery_draft_decision")(
            _node("n20_delivery_gate", []), self.st, {}
        )
        self.assertEqual(route, {"route": "has_p0_maxed"})

    def test_delivery_gate_routes_ready_to_g4(self):
        self.st.write_artifact("delivery_draft", {
            "state": "ready",
            "post_publish_validation": {"has_p0": False, "p0_count": 0},
        })
        route = handlers.get("delivery_draft_decision")(
            _node("n20_delivery_gate", []), self.st, {}
        )
        self.assertEqual(route, {"route": "ready"})

    def _seed_delivery(self):
        self._seed_full_delivery()

    def _seed_full_delivery(
        self, *, ownership=True, collaborator=False, stale_delivery_ref=False
    ):
        self.st.params["run_mode"] = "full"
        draft = {
            "state": "ready",
            "report_version": "0.0",
            "doc_token": "doc_real",
            "url": "https://www.feishu.cn/docx/doc_real",
            "revision_id": 7,
            "report_ref": self.st.artifact_ref("report_md"),
            "ownership_transferred": ownership,
            "collaborator_added": collaborator,
            "collaborator_perm": "full_access" if collaborator else "",
            "delivery_access_granted": ownership or collaborator,
            "readback": {"ok": True, "revision_id": 7},
            "degraded": False,
        }
        self.st.write_artifact("delivery_draft", draft)
        self.st.write_artifact("external_receipts", {
            "doc_token": "doc_real",
            "url": draft["url"],
            "revision_id": 7,
            "report_version": "0.0",
            "ownership_transferred": ownership,
            "collaborator_added": collaborator,
            "collaborator_perm": "full_access" if collaborator else "",
            "delivery_access_granted": ownership or collaborator,
            "degraded": False,
        })
        self.st.write_artifact("feishu_doc", {
            **draft,
            "ownership_transferred": ownership,
            "collaborator_added": collaborator,
            "collaborator_perm": "full_access" if collaborator else "",
            "delivery_access_granted": ownership or collaborator,
        })
        delivery_ref = (
            {"artifact_id": "delivery_draft@v0"}
            if stale_delivery_ref else self.st.artifact_ref("delivery_draft")
        )
        self.st.write_artifact("user_confirmation", {
            "status": "confirmed",
            "report_version": "0.0",
            "report_ref": self.st.artifact_ref("report_md"),
            "delivery_ref": delivery_ref,
            "revision_id": 7,
        })

    def test_delivery_commit_binds_version_chain(self):
        self._seed_delivery()
        res = handlers.get("delivery_commit")(
            _node("n23_delivery_commit", ["delivery_record", "task_result"]),
            self.st, {})
        rec = res["artifacts"]["delivery_record"]
        self.assertTrue(rec["committed"])
        self.assertEqual(rec["report_version"], "0.0")
        self.assertIn("revision_id", rec)
        self.assertEqual(res["artifacts"]["task_result"]["status"], "delivered")

    def test_handoff_validates_and_writes_file(self):
        self._seed_delivery()
        commit = handlers.get("delivery_commit")(
            _node("n23_delivery_commit", ["delivery_record", "task_result"]),
            self.st, {})
        for k, v in commit["artifacts"].items():
            self.st.write_artifact(k, v)
        with patch("handlers.feishu.current_revision", return_value=7):
            res = handlers.get("handoff")(
                _node("n24_handoff", ["handoff"]), self.st, {})
        ho = res["artifacts"]["handoff"]
        self.assertTrue(ho["terminal_accepted"])
        self.assertTrue(ho["validated"])
        self.assertNotIn("demo_validated", ho)
        self.assertFalse(ho["checks"]["revision_changed"])
        # handoff.json 落盘。
        hp = self.st.run_dir / "delivery" / "handoff.json"
        self.assertTrue(hp.exists())
        self.assertTrue(json.loads(hp.read_text())["terminal_accepted"])

    def test_handoff_flags_revision_change(self):
        """复读 revision 与绑定不一致 → validated=False（作废告警）。"""
        self._seed_delivery()
        commit = handlers.get("delivery_commit")(
            _node("n23_delivery_commit", ["delivery_record", "task_result"]),
            self.st, {})
        for k, v in commit["artifacts"].items():
            self.st.write_artifact(k, v)
        orig = feishu.current_revision
        feishu.current_revision = lambda tok: 999  # 模拟文档被改
        try:
            # feishu_doc 非降级才会触发复读；改写为非降级并给绑定 revision。
            fd = self.st.read_artifact("feishu_doc")
            fd["degraded"] = False
            fd["revision_id"] = 1
            self.st.write_artifact("feishu_doc", fd)
            rc = self.st.read_artifact("external_receipts")
            rc["degraded"] = False
            rc["revision_id"] = 1
            self.st.write_artifact("external_receipts", rc)
            res = handlers.get("handoff")(
                _node("n24_handoff", ["handoff"]), self.st, {})
            ho = res["artifacts"]["handoff"]
            self.assertTrue(ho["checks"]["revision_changed"])
            self.assertFalse(ho["validated"])
        finally:
            feishu.current_revision = orig

    def test_full_commit_rejects_missing_ownership(self):
        self._seed_full_delivery(ownership=False)
        with self.assertRaisesRegex(ValueError, "所有权"):
            handlers.get("delivery_commit")(
                _node("n23_delivery_commit", ["delivery_record", "task_result"]),
                self.st,
                {},
            )

    def test_full_commit_and_handoff_accept_collaborator_fallback(self):
        self._seed_full_delivery(ownership=False, collaborator=True)
        commit = handlers.get("delivery_commit")(
            _node("n23_delivery_commit", ["delivery_record", "task_result"]),
            self.st,
            {},
        )
        record = commit["artifacts"]["delivery_record"]
        self.assertTrue(record["committed"])
        self.assertFalse(record["ownership_transferred"])
        self.assertTrue(record["collaborator_added"])
        self.assertTrue(record["delivery_access_granted"])
        for key, value in commit["artifacts"].items():
            self.st.write_artifact(key, value)
        with patch("handlers.feishu.current_revision", return_value=7):
            handoff = handlers.get("handoff")(
                _node("n24_handoff", ["handoff"]), self.st, {}
            )["artifacts"]["handoff"]
        self.assertTrue(handoff["terminal_accepted"])
        self.assertFalse(handoff["checks"]["ownership_transferred"])
        self.assertTrue(handoff["checks"]["collaborator_added"])
        self.assertTrue(handoff["checks"]["delivery_access_granted"])

    def test_full_commit_rejects_stale_draft_ref(self):
        self._seed_full_delivery(stale_delivery_ref=True)
        with self.assertRaisesRegex(ValueError, "用户终验绑定"):
            handlers.get("delivery_commit")(
                _node("n23_delivery_commit", ["delivery_record", "task_result"]),
                self.st,
                {},
            )

    def test_full_handoff_rejects_unverifiable_live_revision(self):
        self._seed_full_delivery()
        commit = handlers.get("delivery_commit")(
            _node("n23_delivery_commit", ["delivery_record", "task_result"]),
            self.st,
            {},
        )
        for key, value in commit["artifacts"].items():
            self.st.write_artifact(key, value)
        with patch("handlers.feishu.current_revision", return_value=None):
            handoff = handlers.get("handoff")(
                _node("n24_handoff", ["handoff"]), self.st, {}
            )["artifacts"]["handoff"]
        self.assertFalse(handoff["checks"]["live_revision_verified"])
        self.assertFalse(handoff["terminal_accepted"])


class TestValidator(unittest.TestCase):
    def test_clean_report_passes(self):
        qc = validator.validate_report({"markdown": _REPORT_MD, "degraded": False})
        self.assertFalse(qc["has_p0"])
        self.assertEqual(qc["p0_count"], 0)
        self.assertEqual(qc["scan_mode"], "full_scan")

    def test_forbidden_term_is_p0(self):
        bad = _REPORT_MD + "\n本次活动转化率高达 30%，人均消费提升。\n"
        qc = validator.validate_report({"markdown": bad})
        self.assertTrue(qc["has_p0"])
        ids = {i["id"] for i in qc["issues"]}
        self.assertIn("P0-forbidden-转化率", ids)
        self.assertIn("P0-forbidden-人均", ids)

    def test_aggregate_sum_is_p0(self):
        bad = _REPORT_MD + "\n多活动合计声量 10 万。\n"
        qc = validator.validate_report({"markdown": bad})
        self.assertTrue(qc["has_p0"])
        self.assertTrue(any(i["id"].startswith("P0-aggregate") for i in qc["issues"]))

    def test_raw_json_and_internal_fields_are_p0(self):
        bad = (
            _REPORT_MD
            + '\n- 证据：{"_annot_id":"internal","interaction_cnt":57945}\n'
        )
        qc = validator.validate_report({"markdown": bad})
        self.assertTrue(qc["has_p0"])
        self.assertTrue(any(
            i["id"].startswith("P0-raw-json") for i in qc["issues"]
        ))

    def test_missing_chapter_is_p0(self):
        qc = validator.validate_report({"markdown": "# 标题\n只有一段话。"})
        self.assertTrue(qc["has_p0"])
        self.assertTrue(any("missing-chapter" in i["id"] for i in qc["issues"]))

    def test_broad_headings_cannot_bypass_complete_structure(self):
        incomplete = (
            "# 报告\n## 品牌传播\n## 活动传播\n## 综合定位\n"
            "## 经验总结\n![图](https://example.com/a.png)\n"
            + ("只有大章，没有固定子章节。" * 30)
        )
        qc = validator.validate_report({"markdown": incomplete})
        self.assertTrue(qc["has_p0"])
        ids = {issue["id"] for issue in qc["issues"]}
        self.assertTrue(any(value.startswith("P0-missing-brand-section") for value in ids))
        self.assertIn("P0-missing-campaign-sections", ids)

    def test_empty_report_is_p0(self):
        qc = validator.validate_report({"markdown": ""})
        self.assertTrue(qc["has_p0"])

    def test_full_rules_detect_placeholder_causality_and_phone(self):
        bad = (
            _REPORT_MD
            + "\n[待补充] 活动带来声量增长。联系人：13800138000\n"
        )
        qc = validator.validate_report({"markdown": bad})
        ids = {issue["id"] for issue in qc["issues"]}
        self.assertTrue(any(value.startswith("P0-placeholder") for value in ids))
        self.assertTrue(any(value.startswith("P0-causality") for value in ids))
        self.assertTrue(any(value.startswith("P0-privacy-phone") for value in ids))

    def test_full_rules_detect_out_of_range_product_funnel(self):
        qc = validator.validate_report(
            {"markdown": _REPORT_MD},
            stats={
                "campaigns": {
                    "测试活动": {
                        "metrics": {"产品提及占比": 1.2},
                        "dimension_nsr": {},
                    }
                }
            },
        )
        self.assertTrue(any(
            issue["id"].startswith("P0-funnel-range")
            for issue in qc["issues"]
        ))

    def test_small_sample_equivalent_disclosures_pass(self):
        stats = {
            "campaigns": {
                "测试活动": {
                    "metrics": {},
                    "dimension_nsr": {
                        "口感": {"sample_size": 19},
                        "周边": {"sample_size": 5},
                        "库存": {"sample_size": 10},
                    },
                }
            }
        }
        report = (
            _REPORT_MD
            + "\n口感样本量不足 30，仅作定性参考。"
            + "\n周边样本数不足，不输出确定性 NSR。"
            + "\n库存样本量<30，不计算 NSR。\n"
        )
        qc = validator.validate_report({"markdown": report}, stats=stats)
        self.assertFalse(any(
            issue["id"].startswith("P0-small-sample")
            for issue in qc["issues"]
        ))

    def test_small_sample_without_disclosure_is_p0(self):
        stats = {
            "campaigns": {
                "测试活动": {
                    "metrics": {},
                    "dimension_nsr": {"口感": {"sample_size": 19}},
                }
            }
        }
        qc = validator.validate_report(
            {"markdown": _REPORT_MD + "\n口感表现较好。\n"},
            stats=stats,
        )
        self.assertTrue(any(
            issue["id"].startswith("P0-small-sample")
            for issue in qc["issues"]
        ))

    def test_available(self):
        self.assertTrue(validator.available())


if __name__ == "__main__":
    unittest.main()
