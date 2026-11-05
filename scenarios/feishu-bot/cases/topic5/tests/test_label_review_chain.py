"""N10 candidate → HC → frozen review → both annotation engines, without network calls."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

_CASE = Path(__file__).resolve().parents[1]
_SKILL = _CASE / "ma-resources" / "skills" / "pipeline-orchestrator"
sys.path.insert(0, str(_SKILL / "tests"))
sys.path.insert(0, str(_SKILL))
sys.path.insert(0, str(_CASE))

import annotate
import handlers
import hc_bridge
import label_review
import topic5_progress_card
from _util import make_state


def _load_hitl():
    stub = types.ModuleType("topic5_runner")
    stub.Topic5CardSenderProtocol = type("Topic5CardSenderProtocol", (), {})
    stub.Topic5Runner = type("Topic5Runner", (), {})
    with patch.dict(sys.modules, {"topic5_runner": stub}):
        spec = importlib.util.spec_from_file_location("_n10_hitl_test", _CASE / "topic5_hitl.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    return module


HITL = _load_hitl()
ROWS = [
    {"id": "post-1", "content_mode": "UGC", "title": "抽奖后去买了",
     "content": "中奖了之后在门店购买了酸奶。", "url": "https://example.com/post/1",
     "author": "不得进入审核证据的个人字段", "relevance": True},
    {"id": "post-2", "content_mode": "PGC", "title": "官方广告",
     "content": "与乒乓球赛事合作。", "url": "https://example.com/post/2", "relevance": True},
    {"id": "post-3", "content_mode": "UGC", "title": "无关帖子",
     "content": "其他品牌活动", "relevance": False},
]


def _model_reply(_system, user, **_kwargs):
    samples = json.loads(user)
    quote_id = next(
        quote["quote_id"] for row in samples for quote in row["quotes"]
        if "中奖了" in quote["excerpt"]
    )
    key = "categories" if '"categories"' in _system else "candidates"
    return {key: [{
        "name": "抽奖参与" if key == "categories" else "购买便利",
        "definition": "正文明确描述参与抽奖或购买行动。",
        "boundary": "仅出现活动话题不纳入；同时命中时按明确行动优先。",
        "evidence_refs": [{
            "quote_id": quote_id, "usage": "representative", "reason": "原句明确出现中奖与购买。",
        }],
    }]}


class LabelReviewChainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = make_state(self.tmp.name)
        self.state.write_artifact("cleaned_data", {"rows": ROWS})
        self.state.write_artifact("annotated_round1", {"rows": ROWS})
        with patch("semantic.llm_available", return_value=True), \
                patch("semantic.chat_json", side_effect=_model_reply):
            for fn in (handlers.ugc_discovery, handlers.opinion_candidates):
                result = fn({"id": "test"}, self.state, {})
                for key, value in result["artifacts"].items():
                    self.state.write_artifact(key, value)
        self.state.human = {"node": "n10_hc_opinion", "expect": ["confirmed_opinion_labels"]}
        self.payload = hc_bridge.write_hc_pending(self.state, self.state.run_dir)
        self.job = types.SimpleNamespace(job_id="job_test", mode="full", task_id="task_test")

    def tearDown(self):
        self.tmp.cleanup()

    def test_generators_filter_ugc_and_relevant_opinions(self):
        context = self.payload["review_context"]
        ugc = context["ugc_category_list"]
        opinion = context["opinion_candidates"]
        self.assertEqual(ugc["sampling"]["population_size"], 2)
        self.assertEqual(opinion["sampling"]["population_size"], 2)
        self.assertTrue(all(
            ref["content_type"] == "UGC"
            for item in ugc["review_items"] for ref in item["evidence"]
        ))
        # UGC filtered row still points to the cleaned_data index.
        for item in ugc["review_items"]:
            for ref in item["evidence"]:
                self.assertEqual(ROWS[ref["source_row_index"]]["id"], ref["post_id"])
        self.assertEqual(ugc["source_subject"], self.state.artifact_ref("cleaned_data"))
        self.assertEqual(opinion["source_subject"], self.state.artifact_ref("annotated_round1"))

    def test_transport_confirmation_and_gateway_snapshot_preserve_versions(self):
        frozen = hc_bridge.build_human_outputs(self.state, "pass")["confirmed_opinion_labels"]
        snapshot = topic5_progress_card.label_snapshot(self.payload)
        for field in ("opinion_review_items", "ugc_review_items", "subject", "review_schema_version"):
            self.assertEqual(snapshot[field], frozen[field])
        self.assertEqual(
            frozen["opinion_review_items"],
            self.payload["review_context"]["opinion_candidates"]["review_items"],
        )
        on_disk = json.loads((self.state.run_dir / "hc_pending.json").read_text())
        self.assertEqual(on_disk, self.payload)

    def test_both_annotation_routes_receive_confirmed_rules(self):
        frozen = hc_bridge.build_human_outputs(self.state, "pass")["confirmed_opinion_labels"]
        self.state.write_artifact("confirmed_opinion_labels", frozen)
        with patch.object(annotate, "annotate_round2", return_value={"coverage": {}}) as call:
            handlers.annotate({"id": "n11", "outputs": ["annotated_data"]}, self.state, {})
        kwargs = call.call_args.kwargs
        rules = {key: kwargs[key] for key in ("opinion_review_items", "ugc_review_items")}
        for builder in (annotate._r2_sys, annotate._r2_prompt_dh):
            prompt = builder(frozen["labels"], frozen["ugc_categories"], **rules)
            self.assertIn(frozen["opinion_review_items"][0]["definition"], prompt)
            self.assertIn(frozen["ugc_review_items"][0]["boundary"], prompt)
            self.assertNotIn("https://example.com/post/1", prompt)

        with patch.object(annotate.datahub, "api_available", return_value=True), \
                patch.object(annotate, "_run_datahub") as dh:
            annotate.annotate_round2(
                [dict(ROWS[0])], opinion_labels=frozen["labels"],
                ugc_categories=frozen["ugc_categories"], threshold=1, **rules,
            )
        self.assertIn(frozen["ugc_review_items"][0]["boundary"], dh.call_args.args[1])
        seen = []
        def chat(system, user, **kwargs):
            seen.append(system)
            return [{"id": row["id"], "opinion": "N/A", "ugc_category": "N/A", "discussion_dimension": "N/A"}
                    for row in json.loads(user)]
        with patch.object(annotate.datahub, "api_available", return_value=False), \
                patch.object(annotate.semantic, "llm_available", return_value=True), \
                patch.object(annotate.semantic, "chat_json", side_effect=chat):
            annotate.annotate_round2(
                [dict(ROWS[0])], opinion_labels=frozen["labels"],
                ugc_categories=frozen["ugc_categories"], **rules,
            )
        self.assertIn(frozen["opinion_review_items"][0]["boundary"], seen[0])

    def test_original_card_details_callbacks_and_legacy_compatibility(self):
        card = HITL.build_hc_card(self.job, "N10", 17, self.payload)
        serialized = json.dumps(card, ensure_ascii=False)
        self.assertIn("原文片段", serialized)
        self.assertIn("门店购买了酸奶", serialized)
        self.assertIn("同时命中", serialized)
        self.assertIn('"event_id": 17', serialized)
        self.assertIn('"decision": "pass"', serialized)
        self.assertIn('"decision": "reject"', serialized)
        self.assertIn('"action": "pause_task"', serialized)
        self.assertEqual(card["schema"], "2.0")
        legacy = {"review_context": {
            "opinion_candidates": {"candidates": ["旧观点"]},
            "ugc_category_list": {"categories": ["旧参与方式"]},
        }}
        old_card = HITL.build_hc_card(self.job, "N10", 17, legacy)
        text = json.dumps(old_card, ensure_ascii=False)
        self.assertIn("旧观点", text)
        self.assertIn("未记录，需补充", text)
        self.assertIn("缺少代表原帖", text)

    def test_maximum_candidates_fit_card_limit_with_original_controls(self):
        context = self.payload["review_context"]
        for key, name_key, count in (("opinion_candidates", "candidates", 8), ("ugc_category_list", "categories", 6)):
            original = context[key]["review_items"][0]
            items = []
            for index in range(count):
                evidence = [{**original["evidence"][0], "reason": "理" * 60, "usage": usage,
                             "excerpt": "原" * 180, "title": "帖" * 120,
                             "url": "https://example.com/" + "x" * 1400}
                            for usage in ("representative", "boundary")]
                items.append({
                    **original, "name": f"候选{index}", "definition": "定" * 100,
                    "boundary": "界" * 180, "evidence": evidence,
                })
            context[key]["review_items"] = items
            context[key][name_key] = [item["name"] for item in items]
        card = HITL.build_hc_card(self.job, "N10", 17, self.payload)
        encoded = json.dumps(card, ensure_ascii=False).encode()
        self.assertLessEqual(len(encoded), 30 * 1024)
        def count_tags(value):
            if isinstance(value, dict):
                return int("tag" in value) + sum(count_tags(item) for item in value.values())
            if isinstance(value, list):
                return sum(count_tags(item) for item in value)
            return 0
        self.assertLess(count_tags(card), 200)
        self.assertEqual(encoded.count(("定" * 100).encode()), 14)
        self.assertEqual(encoded.count(("界" * 180).encode()), 14)


if __name__ == "__main__":
    unittest.main()
