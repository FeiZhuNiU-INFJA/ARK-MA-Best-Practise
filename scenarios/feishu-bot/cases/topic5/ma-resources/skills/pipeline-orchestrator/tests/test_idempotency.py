"""Unit tests for the engine's idempotency execution layer and retry policy,
using hand-built minimal nodes + monkeypatched handlers."""
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shutil
import tempfile
import unittest
from unittest import mock

import engine
import handlers
from _util import make_state


def _mini_dag(nodes):
    """Build a Dag from a bare node list (no yaml)."""
    first = nodes[0]["id"]
    return engine.Dag({"nodes": nodes, "entry": first, "slug": "s", "version": "0.2"})


class TestIdempotentReplay(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    def test_preseeded_ledger_skips_handler(self):
        calls = {"n": 0}

        def fake(node, st, ctx):
            calls["n"] += 1
            return {"artifacts": {"out": {"ok": True}}}

        node = {"id": "sfx", "handler": "fake_sfx", "mode": "tool",
                "side_effect": "external-stat-pull",
                "idempotency": "k:{task_id}", "outputs": ["out"], "next": None}
        dag = _mini_dag([node])
        st = make_state(self.ws, task_id="T", current_node=dag.entry)
        st.ledger["k:T"] = {"node": "sfx", "artifacts": []}  # pre-committed

        with mock.patch.dict(handlers._REGISTRY, {"fake_sfx": fake}):
            engine.run(dag, st)

        self.assertEqual(calls["n"], 0, "handler must NOT run on idempotent replay")
        self.assertEqual(st.status, "completed")
        self.assertTrue(any(e["event"] == "idempotent_replay" for e in st.log))

    def test_commit_records_ledger_after_execution(self):
        def fake(node, st, ctx):
            return {"artifacts": {"out": {"ok": True}}}

        node = {"id": "sfx", "handler": "fake_sfx", "mode": "tool",
                "side_effect": "external-stat-pull",
                "idempotency": "k:{task_id}", "outputs": ["out"], "next": None}
        dag = _mini_dag([node])
        st = make_state(self.ws, task_id="T", current_node=dag.entry)

        with mock.patch.dict(handlers._REGISTRY, {"fake_sfx": fake}):
            engine.run(dag, st)

        self.assertIn("k:T", st.ledger)
        self.assertTrue(any(e["event"] == "idempotent_commit" for e in st.log))

    def test_no_idempotency_when_side_effect_none(self):
        calls = {"n": 0}

        def fake(node, st, ctx):
            calls["n"] += 1
            return {"artifacts": {"out": {"ok": True}}}

        node = {"id": "plain", "handler": "fake_plain", "mode": "code",
                "side_effect": "none", "idempotency": "k:{task_id}",
                "outputs": ["out"], "next": None}
        dag = _mini_dag([node])
        st = make_state(self.ws, task_id="T", current_node=dag.entry)
        st.ledger["k:T"] = {"node": "plain", "artifacts": []}

        with mock.patch.dict(handlers._REGISTRY, {"fake_plain": fake}):
            engine.run(dag, st)
        # side_effect none -> idempotency ignored, handler still runs
        self.assertEqual(calls["n"], 1)
        self.assertEqual(st.status, "completed")


class TestReconcileBeforeRetry(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    def test_exception_but_key_in_external_truth_reconciles(self):
        # 对账查的是外部真相 ctx["reconcile"],不是 st.ledger。
        # ledger 保持为空,否则执行前的 idempotent_replay 会先截走,reconcile 成死代码。
        # handler 真正执行并抛异常 -> 引擎在重试前查 ctx["reconcile"],
        # 键已在外部 -> 写入 ledger、发 reconciled、视为成功推进。
        calls = {"n": 0}

        def boom(node, st, ctx):
            calls["n"] += 1
            raise RuntimeError("external UNKNOWN")

        node = {"id": "sfx", "handler": "fake_boom", "mode": "tool",
                "side_effect": "external-stat-pull",
                "idempotency": "k:{task_id}", "outputs": ["out"],
                "retry": {"max": 0, "on_fail": "pause"}, "next": None}
        dag = _mini_dag([node])
        st = make_state(self.ws, task_id="T", current_node=dag.entry)
        # 外部真相:幂等键 k:T 已落地(渲染自 idempotency="k:{task_id}", task_id="T")
        ctx = {"reconcile": {"k:T": {"external": True}}}

        with mock.patch.dict(handlers._REGISTRY, {"fake_boom": boom}):
            engine.run(dag, st, ctx)

        self.assertEqual(calls["n"], 1, "handler 必须真正执行(无 ledger replay)")
        self.assertEqual(st.status, "completed", "reconcile should treat as success")
        self.assertTrue(any(e["event"] == "reconciled" for e in st.log))
        # 对账走的是 reconcile,不是执行前的 replay
        self.assertFalse(any(e["event"] == "idempotent_replay" for e in st.log))
        self.assertIn("k:T", st.ledger, "对账成功后把键写入 ledger")


class TestRetryPolicy(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    def test_retry_then_succeed(self):
        attempts = {"n": 0}

        def flaky(node, st, ctx):
            attempts["n"] += 1
            if attempts["n"] <= 2:
                raise RuntimeError("transient")
            return {"artifacts": {"out": {"ok": True}}}

        node = {"id": "r", "handler": "fake_flaky", "mode": "code",
                "side_effect": "none", "outputs": ["out"],
                "retry": {"max": 2, "on_fail": "pause"}, "next": None}
        dag = _mini_dag([node])
        st = make_state(self.ws, current_node=dag.entry)
        with mock.patch.dict(handlers._REGISTRY, {"fake_flaky": flaky}):
            engine.run(dag, st)
        self.assertEqual(st.status, "completed")
        self.assertEqual(attempts["n"], 3)

    def test_on_fail_pause_sets_failed(self):
        def boom(node, st, ctx):
            raise RuntimeError("perma")

        node = {"id": "p", "handler": "fake_boom", "mode": "code",
                "side_effect": "none", "outputs": ["out"],
                "retry": {"max": 1, "on_fail": "pause"}, "next": "nextnode"}
        dag = _mini_dag([node])
        st = make_state(self.ws, current_node=dag.entry)
        with mock.patch.dict(handlers._REGISTRY, {"fake_boom": boom}):
            engine.run(dag, st)
        self.assertEqual(st.status, "failed")
        self.assertEqual(st.nodes["p"]["status"], "failed")
        self.assertEqual(st.current_node, "p")  # not advanced

    def test_explicit_retry_requeues_only_failed_node_with_fresh_budget(self):
        attempts = {"n": 0}

        def fixed_after_deploy(node, st, ctx):
            attempts["n"] += 1
            if attempts["n"] <= 2:
                raise RuntimeError("old deployment")
            return {"artifacts": {"out": {"ok": True}}}

        node = {
            "id": "p",
            "handler": "fixed_after_deploy",
            "mode": "code",
            "side_effect": "none",
            "outputs": ["out"],
            "retry": {"max": 1, "on_fail": "pause"},
            "next": None,
        }
        dag = _mini_dag([node])
        st = make_state(self.ws, current_node=dag.entry)
        with mock.patch.dict(
            handlers._REGISTRY, {"fixed_after_deploy": fixed_after_deploy}
        ):
            engine.run(dag, st)
            self.assertEqual(st.status, "failed")
            self.assertEqual(st.nodes["p"]["attempts"], 2)
            engine.retry_failed(dag, st)
        self.assertEqual(st.status, "completed")
        self.assertEqual(st.nodes["p"]["attempts"], 3)
        self.assertEqual(st.read_artifact("out"), {"ok": True})
        self.assertTrue(
            any(item["event"] == "explicit_retry" for item in st.log)
        )

    def test_on_fail_exception_bypass_advances(self):
        def boom(node, st, ctx):
            raise RuntimeError("perma")

        nodes = [
            {"id": "b", "handler": "fake_boom", "mode": "tool",
             "side_effect": "external-stat-pull", "outputs": ["out"],
             "idempotency": "none",
             "retry": {"max": 1, "on_fail": "exception_bypass"}, "next": "sink"},
            {"id": "sink", "handler": "fake_sink", "mode": "code",
             "side_effect": "none", "outputs": [], "next": None},
        ]
        dag = _mini_dag(nodes)
        st = make_state(self.ws, current_node=dag.entry)
        with mock.patch.dict(handlers._REGISTRY,
                             {"fake_boom": boom,
                              "fake_sink": lambda n, s, c: {"artifacts": {}}}):
            engine.run(dag, st)
        self.assertEqual(st.status, "completed")
        self.assertEqual(st.nodes["b"]["status"], "bypassed")


if __name__ == "__main__":
    unittest.main()
