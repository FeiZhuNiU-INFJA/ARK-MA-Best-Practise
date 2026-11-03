"""Regression coverage for customer-reported missing modules and native layout."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from _util import make_state

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import charts
import feishu
import handlers
import report_contract as contract
import validator


class TestReportContract(unittest.TestCase):
    def test_nested_trends_single_and_multiple_platforms(self):
        nested = {"xiaohongshu": {"20261001": 5, "20261002": 7}}
        spec = charts._trend_spec("brand", {
            "trend": nested, "interaction_trend": {"xiaohongshu": {"20261001": 50, "20261002": 70}},
        })
        self.assertEqual(spec["categories"], ["20261001", "20261002"])
        self.assertEqual(spec["series"][1]["values"], [50, 70])
        nested["weibo"] = {"20261001": 3, "20261003": 8}
        self.assertEqual(charts.daily_totals(nested), {"20261001": 8, "20261002": 7, "20261003": 8})
        nested["_total"] = {"20261001": 8, "20261002": 7, "20261003": 8}
        self.assertEqual(charts.daily_totals(nested)["20261001"], 8)

    def test_top10_content_and_privacy(self):
        rows = [{"id": str(i), "content_mode": kind, "title": "",
                 "content": f"真实正文{i}|内容", "author": "公开机构" if kind == "PGC" else "私人昵称",
                 "_platform": "xiaohongshu", "interaction_cnt": i}
                for kind in ("PGC", "UGC") for i in range(12)]
        top = contract.top_content(rows)
        self.assertEqual(len(top["pgc"]), 10)
        self.assertEqual(len(top["ugc"]), 10)
        self.assertEqual(top["pgc"][0]["author"], "公开机构")
        self.assertNotIn("author", top["ugc"][0])
        evidence = handlers._report_evidence({"campaigns": {"测试": {"top_content": top}}})
        self.assertIn("content", evidence["campaigns"]["测试"]["top_content"]["ugc"][0])
        blocks = feishu.md_to_blocks(contract.hot_tables({"top_content": top}))
        tables = [b for b in blocks if b["block_type"] == 31]
        self.assertEqual([len(b["_table_rows"]) for b in tables], [11, 11])
        self.assertEqual([len(b["_table_rows"][0]) for b in tables], [8, 5])
        self.assertIn("|", tables[1]["_table_rows"][1][1])

    def test_opinion_denominator_and_representatives(self):
        rows = [{"content_mode": "UGC", "opinion": "口味好", "ugc_category": "测评",
                 "ai_sentiment": 1, "content": str(i), "interaction_cnt": i}
                for i in range(12)]
        rows[0]["opinion"] = "N/A"
        bundle = handlers._campaign_stats(rows, [], [], True)
        opinion = bundle["opinion_analysis"]["positive"]
        self.assertEqual(opinion["sample_size"], 12)
        self.assertEqual(opinion["top5"][0]["share"], 11 / 12)
        self.assertEqual(len(opinion["top5"][0]["representatives"]), 3)
        self.assertEqual(len(bundle["ugc_analysis"]["categories"][0]["representatives"]), 2)
        self.assertEqual(bundle["metrics"]["NSR"], 1)

    def test_assemble_is_repeatable_and_scanner_enforces_modules(self):
        rows = [{"id": str(i), "content_mode": "PGC" if i < 12 else "UGC",
                 "author": "公开机构" if i < 12 else "私人昵称",
                 "title": "" if i >= 12 else f"标题{i}|Vlog",
                 "content": f"正文{i}口味实测", "url": f"https://example.com/{i}",
                 "site_name": "小红书", "_platform": "xiaohongshu",
                 "_publish_ymd": f"202610{1+i%2:02d}", "interaction_cnt": i,
                 "ai_sentiment": 1 if i < 12 else -1 if i < 23 else 0,
                 "opinion": "口味好" if i < 12 else "体验差", "ugc_category": "测评",
                 "discussion_dimension": "品牌"} for i in range(30)]
        bundle = handlers._campaign_stats(rows, ["口味"], [], True)
        stats = {"brand": {"trend": {"xiaohongshu": {"20261001": 5, "20261002": 7}},
                           "wordcloud": {"xiaohongshu": {"口味": 12}},
                           "top_content": contract.top_content(rows)},
                 "campaigns": {"测试": bundle}, "clean_record": {"total_in": 30, "total_out": 30}}
        plan = {"brand_query": "口味", "activity_query": "实测|活动", "activity_filter": "广告",
                "platforms": ["xiaohongshu"], "period_start": "2026-10-01", "period_end": "2026-10-02"}
        md = "# 测试复盘\n\n## 00 篇头 · 数据说明\n原数据说明\n\n## 整体洞察\n分析样本30条。\n\n## 01 品牌传播表现\n"
        for number, title in enumerate(("传播概况", "口碑分析", "内容分析", "热门内容"), 1):
            md += f"\n### 1.{number} {title}\n分析样本30条。\n"
        md += "\n## 02 活动传播表现\n### 2.1 活动 测试\n"
        for number, title in enumerate(("传播概况", "活动声量渗透分析", "口碑分析", "内容分析", "热门内容"), 1):
            md += f"\n#### 2.1.{number} {title}\n分析样本30条。\n"
        md += "\n## 03 综合表现定位\n### 3.1 基准期对比\n基准不可用。\n## 04 经验总结\n成功经验；待改进问题；行动计划。\n## 05 总结与建议\n依据样本制定建议。\n"
        insight = {"brand": {"overall": {"platforms": {"xiaohongshu": {"topics": [
            {"topic": "口味体验", "words": ["口味"], "insight": "用户关注口味体验。", "action": "跟踪体验反馈。"},
        ]}}}}}
        with tempfile.TemporaryDirectory() as directory:
            payload = {"charts": []}
            for spec in charts.build_chart_specs(stats, {}):
                path = Path(directory) / f"{spec['chart_id']}.png"
                path.write_bytes(b"image")
                payload["charts"].append({**spec, "spec": spec, "local_path": str(path)})
            result = contract.assemble(md, plan=plan, stats=stats, charts=payload, insight=insight)
            again = contract.assemble(result, plan=plan, stats=stats, charts=payload, insight=insight)
            self.assertEqual(result.count("正面观点 TOP5"), 1)
            self.assertEqual(again.count("正面观点 TOP5"), 1)
            self.assertEqual(again.count("核心议题解读"), 1)
            scan = validator.validate_report({"markdown": result}, requirement=plan, stats=stats, charts=payload)
            self.assertEqual(scan["p0_count"], 0, scan["issues"])
            layout = list(feishu._flatten_blocks(feishu.layout_blocks(feishu.md_to_blocks(result))))
            self.assertGreaterEqual(sum(b["block_type"] == 24 for b in layout), 3)
            self.assertGreater(sum(b["block_type"] == 19 for b in layout), 0)
            broken = result + "\n![伪造图](/missing/brand_trend.png)"
            scan = validator.validate_report({"markdown": broken}, requirement=plan, stats=stats, charts=payload)
            self.assertTrue(any(i["id"] == "P0-image-invalid" for i in scan["issues"]))
            missing = {**payload, "charts": [i for i in payload["charts"] if i["chart_id"] != "brand_trend"]}
            scan = validator.validate_report({"markdown": result}, stats=stats, charts=missing)
            self.assertTrue(any(i["id"] == "P0-module-trend-brand" for i in scan["issues"]))

    def test_native_nested_publishing_and_missing_images(self):
        with self.assertRaisesRegex(ValueError, "本地图片不存在"):
            feishu.md_to_blocks("![图片](/missing/image.png)")
        with tempfile.NamedTemporaryFile(suffix=".png") as file:
            file.write(b"image")
            file.flush()
            md = f"### 1.3 内容分析\n**洞察**内容。\n![词云]({file.name})\n**议题**说明。\n"
            blocks = feishu.layout_blocks(feishu.md_to_blocks(md))
            seen = []
            def fake_req(method, path, token, body=None, timeout=30):
                seen.append((method, path, body))
                if method == "POST":
                    return {"children": [{**b, "block_id": f"block-{len(seen)}-{i}",
                                          **({"children": ["column-left", "column-right"]} if b["block_type"] == 24 else {})}
                                         for i, b in enumerate(body["children"])]}
                return {}
            with patch("feishu._req", side_effect=fake_req), patch("feishu._upload_media", return_value="image-token"):
                self.assertEqual(feishu._publish_blocks("token", "doc", blocks), 1)
            parents = [p for _, p, _ in seen]
            self.assertTrue(any("column-left/children" in p for p in parents))
            self.assertTrue(any("column-right/children" in p for p in parents))
            self.assertTrue(any(body and "replace_image" in body for _, _, body in seen))

    def test_brand_cloud_enters_independent_insight(self):
        with tempfile.TemporaryDirectory() as directory:
            state = make_state(directory)
            state.write_artifact("stats_bundle", {"brand": {
                "wordcloud": {"xiaohongshu": {"口味": 12, "酸奶": 10}},
                "hot_content": {"xiaohongshu": [{"id": "brand-post", "content": "酸奶口味好"}]},
            }})
            state.write_artifact("wordcloud_data", {"campaigns": {}})
            import charts
            state.write_artifact("chart_urls", {"charts": [
                {**spec, "wordcloud_artifacts": {"scope": spec["wordcloud_scope"]}}
                for spec in charts.build_chart_specs(state.read_artifact("stats_bundle"), {})
                if spec["chart_type"] == "wordcloud"
            ]})
            with patch("semantic.llm_available", return_value=True), patch(
                "wordcloud_adapter.analyze", return_value={"topics": [{
                    "topic": "口味体验", "words": ["口味"], "evidence_ids": ["brand-post"],
                    "word_frequencies": {"口味": 12},
                    "insight": "关注口味", "action": "跟踪体验",
                }]}
            ):
                result = handlers.wordcloud_insight({"id": "n15"}, state, {})["artifacts"]["wordcloud_insight"]
            topic = result["brand"]["overall"]["platforms"]["xiaohongshu"]["topics"][0]
            self.assertEqual(topic["evidence_ids"], ["brand-post"])
            self.assertEqual(topic["word_frequencies"], {"口味": 12})
            self.assertEqual(result["campaigns"], {})


if __name__ == "__main__":
    unittest.main()
