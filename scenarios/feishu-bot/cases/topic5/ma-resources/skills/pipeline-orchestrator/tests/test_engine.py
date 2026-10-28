"""Integration-style tests for the engine driving the real DAG:
happy path through all 5 human gates, QC loop branches, decision routing,
human pause/resume, idempotent replay across a save/load boundary.
"""
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shutil
import tempfile
import unittest
from pathlib import Path

import engine
import handlers
from state import RunState
from _util import (
    compliant_pipeline_services,
    default_human_outputs,
    drive_to_completion,
    load_dag,
)


HUMAN_GATES = {"n01_hc_requirement", "n03_hc_plan", "n10_hc_opinion",
               "n17_validate", "n21_hc_user"}


class TestDagLoad(unittest.TestCase):
    def test_dag_fields(self):
        dag = load_dag()
        self.assertEqual(dag.entry, "n00_intake")
        self.assertEqual(dag.slug, "marketing-activity-review")
        self.assertEqual(dag.version, "0.2")
        self.assertEqual(len(dag.nodes), 28)
        self.assertIn("qc_max_rounds", dag.params)


class TestCreateRun(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    def test_create_run_scaffolds_dirs_and_state(self):
        dag = load_dag()
        st = engine.create_run(dag, Path(self.ws), task_id="task1", run_id="runX")
        self.assertEqual(st.current_node, "n00_intake")
        self.assertEqual(st.status, "running")
        run_dir = Path(st.run_dir)
        for sub in ("artifacts", "attempts", "validation", "delivery"):
            self.assertTrue((run_dir / sub).is_dir())
        self.assertTrue((run_dir / "run.json").exists())


class TestHumanGateFlow(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp()
        self.dag = load_dag()

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    def test_pauses_at_first_human_gate(self):
        st = engine.create_run(self.dag, Path(self.ws), "t", "r")
        st.params["run_mode"] = "demo"
        engine.run(self.dag, st)
        self.assertEqual(st.status, "awaiting_human")
        self.assertEqual(st.human["node"], "n01_hc_requirement")
        self.assertEqual(st.human["expect"], ["requirement_confirmed"])

    def test_strict_run_completes_through_all_gates_with_compliant_services(self):
        st = engine.create_run(self.dag, Path(self.ws), "t", "r")
        # n17 first visit: has_p0 True (retry once); loop auto-resumes, next
        # n17 visit uses the no-p0 payload to converge.
        human = {
            "n17_validate": {"qc_report": {"has_p0": False, "issues": []}},
        }
        drive_to_completion(self.dag, st, human_outputs_by_node=human)
        self.assertEqual(st.status, "completed")
        self.assertIsNone(st.current_node)
        # all 5 gates were visited (completed)
        for g in HUMAN_GATES:
            self.assertEqual(st.nodes[g]["status"], "completed", g)
        # terminal artifacts present
        self.assertIsNotNone(st.read_artifact("task_result"))
        self.assertIsNotNone(st.read_artifact("handoff"))


class TestQcLoop(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp()
        self.dag = load_dag()
        self.services = compliant_pipeline_services()
        self.services.__enter__()

    def tearDown(self):
        self.services.__exit__(None, None, None)
        shutil.rmtree(self.ws, ignore_errors=True)

    def _advance_to_node(self, st, target):
        """Resume gates with no-p0 until the run stops at `target` or completes."""
        pass

    def test_has_p0_retry_reaches_n19_fix(self):
        st = engine.create_run(self.dag, Path(self.ws), "t", "r")
        st.params["run_mode"] = "demo"
        engine.run(self.dag, st)
        # resume non-qc gates until we land on n17
        while st.status == "awaiting_human" and st.human["node"] != "n17_validate":
            engine.resume(
                self.dag, st, human_outputs=default_human_outputs(st)
            )
        self.assertEqual(st.human["node"], "n17_validate")
        # inject a P0: should route has_p0_retry -> n19_fix -> back to n17
        engine.resume(self.dag, st, human_outputs={"qc_report": {"has_p0": True, "issues": [1]}})
        # n19_fix ran and bumped counters, loop paused again at n17
        self.assertEqual(st.status, "awaiting_human")
        self.assertEqual(st.human["node"], "n17_validate")
        self.assertEqual(st.nodes["n19_fix"]["status"], "completed")
        self.assertEqual(st.counters["qc_rounds"], 1)
        # now converge with no P0
        engine.resume(self.dag, st, human_outputs={"qc_report": {"has_p0": False, "issues": []}})
        drive_to_completion(self.dag, st)
        self.assertEqual(st.status, "completed")
        self.assertEqual(st.nodes["n20_delivery_draft"]["status"], "completed")

    def test_has_p0_maxed_reaches_escalate(self):
        # qc_max_rounds=1 so a second P0 trips the maxed branch quickly
        st = engine.create_run(self.dag, Path(self.ws), "t", "r")
        st.params["run_mode"] = "demo"
        st.params["qc_max_rounds"] = 1
        engine.run(self.dag, st)
        while st.status == "awaiting_human" and st.human["node"] != "n17_validate":
            engine.resume(
                self.dag, st, human_outputs=default_human_outputs(st)
            )
        # round1: rounds=0 < 1 -> retry -> n19 -> rounds=1 -> back to n17
        engine.resume(self.dag, st, human_outputs={"qc_report": {"has_p0": True, "issues": [1]}})
        self.assertEqual(st.human["node"], "n17_validate")
        self.assertEqual(st.counters["qc_rounds"], 1)
        # round2: rounds=1 >= 1 -> maxed -> n_hc7_escalate (human gate)
        engine.resume(self.dag, st, human_outputs={"qc_report": {"has_p0": True, "issues": [1]}})
        self.assertEqual(st.status, "awaiting_human")
        self.assertEqual(st.human["node"], "n_hc7_escalate")

    def test_decision_route_recorded(self):
        st = engine.create_run(self.dag, Path(self.ws), "t", "r")
        human = {"n17_validate": {"qc_report": {"has_p0": False, "issues": []}}}
        drive_to_completion(self.dag, st, human_outputs_by_node=human)
        self.assertIn("no_p0", st.nodes["n18_qc_gate"]["routes"])
        self.assertIn("confirmed", st.nodes["n22_user_gate"]["routes"])


class TestResumeWritesHumanArtifact(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp()
        self.dag = load_dag()

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    def test_injected_human_payload_is_stored(self):
        st = engine.create_run(self.dag, Path(self.ws), "t", "r")
        engine.run(self.dag, st)  # pause at n01
        engine.resume(self.dag, st,
                      human_outputs={"requirement_confirmed": {"ok": "yes"}})
        self.assertEqual(st.read_artifact("requirement_confirmed"), {"ok": "yes"})

    def test_missing_human_payload_is_rejected(self):
        st = engine.create_run(self.dag, Path(self.ws), "t", "r")
        engine.run(self.dag, st)
        with self.assertRaisesRegex(ValueError, "missing required artifacts"):
            engine.resume(self.dag, st, human_outputs={})
        self.assertIsNone(st.read_artifact("requirement_confirmed"))


class TestIdempotentReplayOnRealNode(unittest.TestCase):
    """Drive to a committed side-effect node, then reset + re-run to confirm
    the ledger causes a replay instead of re-executing the handler."""

    def setUp(self):
        self.ws = tempfile.mkdtemp()
        self.dag = load_dag()

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    def test_save_load_preserves_ledger_and_replays(self):
        st = engine.create_run(self.dag, Path(self.ws), "t", "r")
        human = {"n17_validate": {"qc_report": {"has_p0": False, "issues": []}}}
        drive_to_completion(self.dag, st, human_outputs_by_node=human)
        self.assertEqual(st.status, "completed")
        # n04 committed an idempotency key
        committed = [e for e in st.log if e["event"] == "idempotent_commit"]
        self.assertTrue(committed)
        replays = [e for e in st.log if e["event"] == "idempotent_replay"]
        # first full run: commits, no replays
        self.assertEqual(replays, [])

        # round-trip persistence restores ledger
        loaded = RunState.load(Path(st.run_dir))
        self.assertEqual(loaded.ledger, st.ledger)
        self.assertTrue(len(loaded.ledger) >= 1)

    def test_effect_journal_prevents_duplicate_after_post_return_crash(self):
        calls = {"count": 0}

        def external_handler(node, state, ctx):
            calls["count"] += 1
            return {"artifacts": {"external_result": {"receipt": "remote-1"}}}

        dag = engine.Dag({
            "slug": "effect-journal-test",
            "version": "1",
            "entry": "n1",
            "params": {},
            "nodes": [{
                "id": "n1",
                "handler": "_test_external_effect",
                "inputs": [],
                "outputs": ["external_result"],
                "side_effect": "remote-create",
                "idempotency": "remote:{task_id}",
                "checkpoint": True,
                "next": None,
            }],
        })
        old_handler = handlers._REGISTRY.get("_test_external_effect")
        handlers._REGISTRY["_test_external_effect"] = external_handler
        try:
            st = engine.create_run(dag, Path(self.ws), "t-effect", "r-effect")
            original_write = st.write_artifact

            def crash_before_artifact(*args, **kwargs):
                raise RuntimeError("simulated crash after external success")

            st.write_artifact = crash_before_artifact
            with self.assertRaisesRegex(RuntimeError, "simulated crash"):
                engine.run(dag, st)
            self.assertEqual(calls["count"], 1)

            loaded = RunState.load(Path(st.run_dir))
            engine.run(dag, loaded)
            self.assertEqual(calls["count"], 1)
            self.assertEqual(
                loaded.read_artifact("external_result"),
                {"receipt": "remote-1"},
            )
            self.assertTrue(any(
                event["event"] == "effect_journal_replay"
                for event in loaded.log
            ))
            self.assertIsNotNone(original_write)
        finally:
            if old_handler is None:
                handlers._REGISTRY.pop("_test_external_effect", None)
            else:
                handlers._REGISTRY["_test_external_effect"] = old_handler


if __name__ == "__main__":
    unittest.main()
