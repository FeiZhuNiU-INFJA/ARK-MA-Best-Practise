"""Unit tests for state.py: artifact versioning, idempotency rendering,
persistence round-trip, _Missing fallback, node_rec."""
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from state import RunState, _Missing, register_run_dir
from _util import make_state


class TestMissing(unittest.TestCase):
    def test_missing_returns_sentinel(self):
        m = _Missing({"a": 1})
        self.assertEqual("{a}".format_map(m), "1")
        self.assertEqual("{b}".format_map(m), "∅")


class TestArtifactVersioning(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp()
        self.st = make_state(self.ws)

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    def test_same_key_twice_is_immutable_versioned(self):
        ref1 = self.st.write_artifact("k", {"v": "a"})
        ref2 = self.st.write_artifact("k", {"v": "b"})
        self.assertEqual(ref1, "k@v1")
        self.assertEqual(ref2, "k@v2")
        base = Path(self.st.run_dir) / "artifacts" / "k"
        self.assertTrue((base / "v1" / "payload.json").exists())
        self.assertTrue((base / "v2" / "payload.json").exists())
        self.assertEqual(json.loads((base / "v1" / "payload.json").read_text())["v"], "a")
        self.assertEqual(json.loads((base / "v2" / "payload.json").read_text())["v"], "b")

    def test_read_artifact_returns_latest(self):
        self.st.write_artifact("k", {"v": "a"})
        self.st.write_artifact("k", {"v": "b"})
        self.assertEqual(self.st.read_artifact("k"), {"v": "b"})

    def test_read_missing_returns_none(self):
        self.assertIsNone(self.st.read_artifact("nope"))

    def test_invalidate_artifacts_removes_string_and_dict_ledger_refs(self):
        ref = self.st.write_artifact("feishu_doc", {"doc_token": "doc-1"})
        artifact_ref = self.st.artifact_ref("feishu_doc")
        self.st.ledger = {
            "legacy": {"node": "n20", "artifacts": [ref]},
            "current": {"node": "n20", "artifacts": [artifact_ref]},
            "unrelated": {"node": "n14", "artifacts": ["chart_urls@v1"]},
        }

        self.st.invalidate_artifacts(["feishu_doc"])

        self.assertIsNone(self.st.read_artifact("feishu_doc"))
        self.assertNotIn("legacy", self.st.ledger)
        self.assertNotIn("current", self.st.ledger)
        self.assertIn("unrelated", self.st.ledger)


class TestIdempotencyRender(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp()
        self.st = make_state(self.ws, task_id="T", run_id="R")

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    def test_none_or_missing_template_returns_none(self):
        self.assertIsNone(self.st.render_idempotency({}))
        self.assertIsNone(self.st.render_idempotency({"idempotency": "none"}))

    def test_renders_with_task_and_run_id(self):
        key = self.st.render_idempotency({"idempotency": "x:{task_id}:{run_id}"})
        self.assertEqual(key, "x:T:R")

    def test_params_injected_and_resource_lock(self):
        self.st.params["resource_lock"] = "LOCK9"
        key = self.st.render_idempotency({"idempotency": "ssjh:{task_id}:{resource_lock}"})
        self.assertEqual(key, "ssjh:T:LOCK9")

    def test_missing_field_falls_back_to_sentinel(self):
        key = self.st.render_idempotency({"idempotency": "x:{resource_lock}"})
        self.assertEqual(key, "x:∅")

    def test_confirmed_plan_version_used(self):
        self.st.write_artifact("confirmed_plan", {"version": "7"})
        key = self.st.render_idempotency({"idempotency": "runinit:{task_id}:{confirmed_plan_version}"})
        self.assertEqual(key, "runinit:T:7")

    def test_report_artifact_sha_changes_when_payload_revision_changes(self):
        node = {
            "idempotency":
                "feishu:{task_id}:{resource_lock}:{report_artifact_sha256}"
        }
        self.st.params["resource_lock"] = "LOCK9"
        self.st.write_artifact(
            "report_md", {"version": "0.2", "markdown": "first"}
        )
        first = self.st.render_idempotency(node)
        self.st.write_artifact(
            "report_md", {"version": "0.2", "markdown": "revised"}
        )
        second = self.st.render_idempotency(node)

        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("feishu:T:LOCK9:"))
        self.assertTrue(second.startswith("feishu:T:LOCK9:"))


class TestNodeRec(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp()
        self.st = make_state(self.ws)

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    def test_node_rec_defaults_and_persists(self):
        rec = self.st.node_rec("n")
        self.assertEqual(rec, {"status": "pending", "attempts": 0, "artifacts": []})
        rec["attempts"] = 3
        self.assertEqual(self.st.node_rec("n")["attempts"], 3)


class TestPersistenceRoundTrip(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    def test_save_load_restores_fields(self):
        st = make_state(self.ws, task_id="TT", run_id="RR")
        st.status = "awaiting_human"
        st.current_node = "n05"
        st.node_rec("n00")["status"] = "completed"
        st.ledger["k1"] = {"node": "n04", "artifacts": ["x@v1"]}
        st.event("n00", "completed", "ok")
        st.save()

        run_dir = Path(st.run_dir)
        loaded = RunState.load(run_dir)
        self.assertEqual(loaded.status, "awaiting_human")
        self.assertEqual(loaded.current_node, "n05")
        self.assertEqual(loaded.nodes["n00"]["status"], "completed")
        self.assertEqual(loaded.ledger["k1"]["node"], "n04")
        self.assertTrue(any(e["event"] == "completed" for e in loaded.log))

    def test_register_run_dir_enables_run_dir_property(self):
        run_dir = Path(self.ws) / "explicit"
        run_dir.mkdir(parents=True)
        register_run_dir("RID", run_dir)
        st = RunState(run_id="RID", task_id="t", dag_slug="s", dag_version="0.2",
                      entry="e", params={})
        self.assertEqual(Path(st.run_dir), run_dir)


if __name__ == "__main__":
    unittest.main()
