"""Cross-process (cold-resume) regression tests.

MA 语义：每次 Session 续跑都是一个全新进程，内存态全丢，只能从 run.json
重建 RunState。以下用例在每个 pause/resume 之间强制走 save→load 边界
（丢弃内存 st，`RunState.load(run_dir)` 重新水化），验证 durable workflow 的
“续跑/断点恢复”在冷进程下也成立：frontier / ledger / counters / 人工关卡
均随 run.json 持久化并被忠实恢复，QC 环也能跨进程重入直至收敛。
"""
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shutil
import tempfile
import unittest
from pathlib import Path

import engine
from state import RunState
from _util import compliant_pipeline_services, default_human_outputs, load_dag


class TestColdResumeAcrossProcessBoundary(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp()
        self.dag = load_dag()
        self.services = compliant_pipeline_services()
        self.services.__enter__()

    def tearDown(self):
        self.services.__exit__(None, None, None)
        shutil.rmtree(self.ws, ignore_errors=True)

    def _reload(self, run_dir):
        """模拟冷进程：丢弃内存 st，仅凭 run.json 重建。"""
        return RunState.load(Path(run_dir))

    def test_cold_resume_through_all_gates_completes(self):
        st = engine.create_run(self.dag, Path(self.ws), task_id="t", run_id="r")
        st.params["run_mode"] = "demo"
        st.save()
        run_dir = st.run_dir
        engine.run(self.dag, st)
        self.assertEqual(st.status, "awaiting_human")

        qc_payload = {"qc_report": {"has_p0": False, "issues": []}}
        steps = 0
        while st.status == "awaiting_human" and steps < 60:
            # 冷进程边界：丢弃上一进程的内存态，从磁盘重建
            st = self._reload(run_dir)
            self.assertEqual(st.status, "awaiting_human")
            outs = (
                qc_payload if st.human["node"] == "n17_validate"
                else default_human_outputs(st)
            )
            engine.resume(self.dag, st, human_outputs=outs)
            steps += 1

        # 收尾后再冷读一次，确认终态落盘
        final = self._reload(run_dir)
        self.assertEqual(final.status, "completed")
        self.assertIsNone(final.current_node)
        self.assertIsNotNone(final.read_artifact("handoff"))

    def test_ledger_and_counters_survive_each_reload(self):
        """QC 环跨冷进程重入：每次 resume 前重载，counters/ledger 必须延续。"""
        st = engine.create_run(self.dag, Path(self.ws), task_id="t", run_id="r")
        st.params["run_mode"] = "demo"
        st.save()
        run_dir = st.run_dir
        engine.run(self.dag, st)

        # 冷进程推进到 n17（非 QC 关卡用空产物续跑）
        while True:
            st = self._reload(run_dir)
            if st.status != "awaiting_human" or st.human["node"] == "n17_validate":
                break
            engine.resume(
                self.dag, st, human_outputs=default_human_outputs(st)
            )
        self.assertEqual(st.human["node"], "n17_validate")

        # 第一轮注入 P0 → 走 has_p0_retry → n19_fix → 回到 n17
        engine.resume(self.dag, st,
                      human_outputs={"qc_report": {"has_p0": True, "issues": [1]}})
        # 冷读确认 QC 轮次与修复节点状态落盘
        mid = self._reload(run_dir)
        self.assertEqual(mid.status, "awaiting_human")
        self.assertEqual(mid.human["node"], "n17_validate")
        self.assertEqual(mid.counters.get("qc_rounds"), 1)
        self.assertEqual(mid.nodes["n19_fix"]["status"], "completed")

        # 记录此刻 ledger 快照，收敛后它只应增长、不应丢失已提交键
        ledger_before = dict(mid.ledger)

        # 第二轮无 P0 收敛，仍走冷进程边界
        engine.resume(self.dag, mid,
                      human_outputs={"qc_report": {"has_p0": False, "issues": []}})
        while True:
            st = self._reload(run_dir)
            if st.status != "awaiting_human":
                break
            engine.resume(
                self.dag, st, human_outputs=default_human_outputs(st)
            )

        final = self._reload(run_dir)
        self.assertEqual(final.status, "completed")
        self.assertGreaterEqual(final.counters.get("qc_rounds"), 1)
        # 幂等账本单调：先前提交的键在后续冷进程中全部保留
        for k, v in ledger_before.items():
            self.assertIn(k, final.ledger, f"ledger 键跨冷进程丢失: {k}")

    def test_idempotent_replay_fires_on_cold_rerun(self):
        """冷重跑一个已提交副作用节点：ledger 命中 → idempotent_replay，handler 不重放。"""
        st = engine.create_run(self.dag, Path(self.ws), task_id="t", run_id="r")
        st.params["run_mode"] = "demo"
        st.save()
        run_dir = st.run_dir
        qc_payload = {"qc_report": {"has_p0": False, "issues": []}}
        while True:
            st = self._reload(run_dir)
            if st.status not in ("running", "awaiting_human"):
                break
            if st.status == "awaiting_human":
                outs = (
                    qc_payload if st.human["node"] == "n17_validate"
                    else default_human_outputs(st)
                )
                engine.resume(self.dag, st, human_outputs=outs)
            else:
                engine.run(self.dag, st)
        done = self._reload(run_dir)
        self.assertEqual(done.status, "completed")
        committed = {e["detail"] for e in done.log if e["event"] == "idempotent_commit"}
        self.assertTrue(committed, "首跑应至少提交一个幂等键")

        # 冷重跑：把终态重置为可再次调度那个已提交的副作用节点，
        # 账本保留 → 命中 replay，handler 不应再次执行。
        replay = self._reload(run_dir)
        # 取第一个被提交幂等键对应的节点，强制回放它
        key0 = next(iter(committed))
        node_id = replay.ledger[key0]["node"]
        replay.status = "running"
        replay.frontier = [node_id]
        replay.current_node = node_id
        replay.nodes[node_id]["status"] = "pending"
        replay.save()

        reloaded = self._reload(run_dir)
        engine.run(self.dag, reloaded)
        self.assertTrue(
            any(e["event"] == "idempotent_replay" and e["detail"] == key0
                for e in reloaded.log),
            "已提交副作用节点冷重跑必须触发 idempotent_replay",
        )


if __name__ == "__main__":
    unittest.main()
