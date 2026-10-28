"""用户上传活动明细 Excel 的列映射测试（不依赖 openpyxl）。"""
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

try:
    from openpyxl import Workbook
except ImportError:
    Workbook = None

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import excel_source


class TestExcelRowMapping(unittest.TestCase):
    def test_maps_customer_sample_columns(self):
        row = excel_source.normalize_excel_row(
            {
                "id": "6ab9ad6a",
                "数据类型": "电商笔记",
                "分析对象": "瑞幸联名",
                "站点名称": "小红书",
                "标题": "多栋冰箱贴太可爱了",
                "内容": "瑞幸联名新品",
                "内容类型": "UGC",
                "话题": "#比格多栋 #瑞幸联名|暹罗厘普",
                "作者": "summer",
                "url": "https://www.xiaohongshu.com/discovery/item/1",
                "发表时间": datetime(2026, 9, 28, 7, 57, 30),
                "总互动量": 91,
                "转发数": 2,
                "评论数": 0,
                "点赞数": 10,
                "阅读数": 75,
                "收藏数": 4,
                "粉丝数": 12345,
            }
        )
        self.assertEqual(row["_platform"], "xiaohongshu")
        self.assertEqual(row["content_mode"], "UGC")
        self.assertEqual(row["interaction_cnt"], 91)
        self.assertEqual(row["fans"], 12345)
        self.assertEqual(
            row["keywords"], ["比格多栋", "瑞幸联名", "暹罗厘普"]
        )
        self.assertIsInstance(row["publish_time"], int)
        self.assertEqual(row["_source"], "user_uploaded_excel")

    def test_computes_interaction_when_total_missing(self):
        row = excel_source.normalize_excel_row(
            {
                "id": "x",
                "站点名称": "小红书",
                "内容类型": "PGC",
                "转发数": 2,
                "评论数": 3,
                "点赞数": 5,
                "收藏数": 7,
            }
        )
        self.assertEqual(row["interaction_cnt"], 17)
        self.assertEqual(row["content_mode"], "PGC")

    def test_rejects_unsafe_mount_path(self):
        with self.assertRaisesRegex(ValueError, "mount_path"):
            excel_source.resolve_activity_xlsx(
                {"source_file": {"mount_path": "../outside.xlsx"}}
            )

    def _write_xlsx(
        self, headers, *, analysis_objects=None, content_modes=None
    ):
        fd, name = tempfile.mkstemp(suffix=".xlsx")
        os.close(fd)
        path = Path(name)
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(headers)
        analysis_objects = analysis_objects or ["测试活动"]
        content_modes = content_modes or ["UGC"] * len(analysis_objects)
        for index, (analysis_object, content_mode) in enumerate(
            zip(analysis_objects, content_modes), 1
        ):
            values = {
                "站点名称": "小红书",
                "标题": f"标题{index}",
                "内容": "正文",
                "发表时间": "2026-01-02",
                "内容类型": content_mode,
                "分析对象": analysis_object,
                "url": f"https://example.com/post/{index}",
                "总互动量": 12,
            }
            sheet.append([values.get(header) for header in headers])
        workbook.save(path)
        workbook.close()
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        return path

    def _plan(self):
        return {
            "source_file": {"name": "test.xlsx", "mount_path": "test.xlsx"},
            "content_scope": "all",
            "platforms": ["xiaohongshu"],
            "campaigns": [{"id": "测试活动"}],
            "period_start": "2026-01-01",
            "period_end": "2026-01-07",
        }

    @unittest.skipUnless(Workbook is not None, "openpyxl not installed")
    def test_load_rejects_missing_analysis_object_or_interaction_columns(self):
        base = ["站点名称", "标题", "内容", "发表时间", "内容类型", "url"]
        for headers, expected in (
            (base + ["总互动量"], "分析对象"),
            (base + ["分析对象"], "互动量列"),
        ):
            with self.subTest(expected=expected):
                path = self._write_xlsx(headers)
                with patch.dict(
                    os.environ, {"TOPIC5_ACTIVITY_XLSX_PATH": str(path)}
                ):
                    with self.assertRaisesRegex(ValueError, expected):
                        excel_source.load_activity_rows(self._plan())

    @unittest.skipUnless(Workbook is not None, "openpyxl not installed")
    def test_load_validates_scope_and_returns_reconciliation(self):
        path = self._write_xlsx([
            "站点名称", "标题", "内容", "发表时间", "内容类型",
            "分析对象", "url", "总互动量",
        ])
        with patch.dict(
            os.environ, {"TOPIC5_ACTIVITY_XLSX_PATH": str(path)}
        ):
            result = excel_source.load_activity_rows(self._plan())
        self.assertEqual(result["meta"]["source_row_count"], 1)
        self.assertEqual(result["meta"]["accepted_row_count"], 1)
        self.assertEqual(result["meta"]["campaigns"], ["测试活动"])

    @unittest.skipUnless(Workbook is not None, "openpyxl not installed")
    def test_single_source_group_maps_to_single_confirmed_activity(self):
        path = self._write_xlsx([
            "站点名称", "标题", "内容", "发表时间", "内容类型",
            "分析对象", "url", "总互动量",
        ], analysis_objects=["暹罗厘普"])
        with patch.dict(
            os.environ, {"TOPIC5_ACTIVITY_XLSX_PATH": str(path)}
        ):
            result = excel_source.load_activity_rows(self._plan())
        self.assertEqual(result["rows"][0]["analysis_object"], "测试活动")
        self.assertEqual(
            result["rows"][0]["_source_analysis_object"], "暹罗厘普"
        )
        self.assertEqual(
            result["meta"]["campaign_mapping"],
            {"暹罗厘普": "测试活动"},
        )

    @unittest.skipUnless(Workbook is not None, "openpyxl not installed")
    def test_multiple_source_groups_still_require_exact_confirmation(self):
        path = self._write_xlsx([
            "站点名称", "标题", "内容", "发表时间", "内容类型",
            "分析对象", "url", "总互动量",
        ], analysis_objects=["活动甲", "活动乙"])
        with patch.dict(
            os.environ, {"TOPIC5_ACTIVITY_XLSX_PATH": str(path)}
        ):
            with self.assertRaisesRegex(ValueError, "不一致"):
                excel_source.load_activity_rows(self._plan())

    @unittest.skipUnless(Workbook is not None, "openpyxl not installed")
    def test_unknown_content_mode_is_valid_and_counted_separately(self):
        path = self._write_xlsx([
            "站点名称", "标题", "内容", "发表时间", "内容类型",
            "分析对象", "url", "总互动量",
        ], content_modes=["未知"])
        with patch.dict(
            os.environ, {"TOPIC5_ACTIVITY_XLSX_PATH": str(path)}
        ):
            result = excel_source.load_activity_rows(self._plan())
        self.assertEqual(result["rows"][0]["content_mode"], "UKN")
        self.assertEqual(
            result["meta"]["invalid_content_modes_mapped_to_unknown"], {}
        )

    @unittest.skipUnless(Workbook is not None, "openpyxl not installed")
    def test_large_invalid_content_mode_ratio_is_rejected(self):
        path = self._write_xlsx([
            "站点名称", "标题", "内容", "发表时间", "内容类型",
            "分析对象", "url", "总互动量",
        ], analysis_objects=["测试活动"] * 20,
            content_modes=["媒体"] + ["UGC"] * 19)
        with patch.dict(
            os.environ, {"TOPIC5_ACTIVITY_XLSX_PATH": str(path)}
        ):
            with self.assertRaisesRegex(ValueError, "大量非 PGC/UGC/未知"):
                excel_source.load_activity_rows(self._plan())

    @unittest.skipUnless(Workbook is not None, "openpyxl not installed")
    def test_small_invalid_content_mode_ratio_maps_to_unknown(self):
        path = self._write_xlsx([
            "站点名称", "标题", "内容", "发表时间", "内容类型",
            "分析对象", "url", "总互动量",
        ], analysis_objects=["测试活动"] * 21,
            content_modes=["媒体"] + ["UGC"] * 20)
        with patch.dict(
            os.environ, {"TOPIC5_ACTIVITY_XLSX_PATH": str(path)}
        ):
            result = excel_source.load_activity_rows(self._plan())
        self.assertEqual(result["rows"][0]["content_mode"], "UKN")
        self.assertEqual(
            result["meta"]["invalid_content_modes_mapped_to_unknown"],
            {"媒体": 1},
        )


if __name__ == "__main__":
    unittest.main()
