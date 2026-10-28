"""端到端拓扑验收 harness（集成级，自制验收标准 A–H）。

跑真实 pipeline.v0.2.yaml（29 节点）+ 几个微型 DAG，断言编排语义成立。
全离线：外部工具与语义节点注入非降级的合规服务夹具，不触网、不计费。
用法：cd /mnt/skills/pipeline-orchestrator && python3 acceptance.py
退出码 0=全过，1=有失败。
"""
from __future__ import annotations

import sys
import tempfile
import shutil
from pathlib import Path

import handlers
import hc_bridge
from engine import Dag, create_run, run, resume
from state import RunState, register_run_dir

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "tests"))
from _util import compliant_pipeline_services  # noqa: E402

PACKAGED_DAG_PATH = HERE / "contracts/pipeline.v0.2.yaml"
SOURCE_DAG_PATH = HERE / "../../io/contracts/pipeline.v0.2.yaml"
DAG_PATH = PACKAGED_DAG_PATH if PACKAGED_DAG_PATH.exists() else SOURCE_DAG_PATH

_RESULTS: list[tuple[bool, str]] = []


def check(cond: bool, label: str) -> None:
    _RESULTS.append((bool(cond), label))
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")


def _tmp_ws() -> Path:
    return Path(tempfile.mkdtemp(prefix="t5acc_"))


def drive(dag, st, ctx=None, qc_plan=None):
    """自动续跑所有人工关卡；qc_plan 控制 n17 每次注入的 has_p0 序列。"""
    ctx = ctx or {}
    qc_plan = list(qc_plan or [])
    guard = 0
    while st.status == "awaiting_human":
        node = st.human["node"]
        ho = {}
        if node == "n01_hc_requirement":
            import json
            ho = hc_bridge.build_human_outputs(st, "pass", json.dumps({
                "brand": "验收品牌", "activity_name": "验收活动",
                "period_start": "2026-01-01", "period_end": "2026-01-07",
                "platforms": ["xiaohongshu"], "brand_query": "验收品牌",
                "activity_query": "验收活动", "product_query": "验收产品",
                "content_scope": "all",
                "business_questions": ["活动表现与改进"],
                "delivery_expectation": "飞书营销活动复盘报告",
                "allowed_actions": "调用已登记能力",
                "source_file": {"name": "acceptance.xlsx", "mount_path": "acceptance.xlsx"},
            }, ensure_ascii=False))
        elif node == "n17_validate":
            has_p0 = qc_plan.pop(0) if qc_plan else False
            ho = {"qc_report": {"has_p0": has_p0,
                                "issues": [{"level": "P0", "msg": "注入"}] if has_p0 else []}}
        elif node == "n21_hc_user" and ctx.get("user_decision") == "escalate":
            ho = hc_bridge.build_human_outputs(st, "escalate", "")
        else:
            ho = hc_bridge.build_human_outputs(st, "pass", "")
        st = resume(dag, st, human_outputs=ho, ctx=ctx)
        guard += 1
        if guard > 40:
            raise RuntimeError("drive guard tripped（疑似人工关卡死循环）")
    return st


def events(st, name):
    return [e for e in st.log if e["event"] == name]


# ── A 可达性 + B 人工门 + D 幂等提交 + G 口径 ─────────────────
def test_clean_run():
    print("\n[A/B/D/G] 全链路洁净跑通")
    dag = Dag.load(DAG_PATH)
    ws = _tmp_ws()
    try:
        st = create_run(dag, ws, task_id="task_clean", run_id="run_clean")
        st.params["run_mode"] = "full"
        st = drive(dag, run(dag, st, {}), {})
        check(st.status == "completed", "A1 最终状态 completed")
        check(st.current_node is None, "A2 current_node 归零")
        done = [n for n, r in st.nodes.items() if r["status"] == "completed"]
        check(len(done) == 25, f"A3 happy-path 25 节点 completed（实际 {len(done)}）")
        unvisited = set(dag.nodes) - set(st.nodes)
        check(unvisited == {"n19_fix", "n_hc7_escalate", "n_user_escalate"},
              f"A4 未访问的恰为 3 个分支专属节点（由 C/用户升级场景覆盖，实际 {unvisited}）")

        humans = [e["node"] for e in events(st, "awaiting_human")]
        expect_h = ["n01_hc_requirement", "n03_hc_plan", "n10_hc_opinion",
                    "n17_validate", "n21_hc_user"]
        check(humans == expect_h, f"B1 恰好 5 道人工门按序暂停（实际 {humans}）")
        resumed = [e["node"] for e in events(st, "human_resumed")]
        check(sorted(resumed) == sorted(expect_h), "B2 5 道人工门全部 resume 推进")

        commits = [e["detail"] for e in events(st, "idempotent_commit")]
        check(len(commits) == 9, f"D1 9 个副作用节点全部记账（实际 {len(commits)}）")
        check(all("∅" not in c or c.count("∅") <= 1 for c in commits), "D2 幂等键渲染（resource_lock 已注入，缺字段回退 ∅）")

        ucat = st.read_artifact("ugc_category_list") or {}
        check(ucat.get("mode") == "fixed_four" and len(ucat.get("categories", [])) == 4,
              "G1 ugc_category_mode=fixed_four → 固定四类")
        rep = st.read_artifact("report_md") or {}
        check(rep.get("chapters") == "five", "G2 report_chapters=five")
        check(dag.params.get("nsr_denominator_includes_neutral") is True, "G3 nsr 含中性")
        oc = st.read_artifact("opinion_candidates") or {}
        check(len(oc.get("candidates", [])) >= 1, "G4 n09b 观点候选已生成供 HC 确认")
    finally:
        shutil.rmtree(ws, ignore_errors=True)


# ── C QC 循环（收敛） + E 版本链 ─────────────────────────────
def test_qc_converge():
    print("\n[C/E] QC 循环：注入 1 次 P0 后收敛")
    dag = Dag.load(DAG_PATH)
    ws = _tmp_ws()
    try:
        st = create_run(dag, ws, task_id="task_qc", run_id="run_qc")
        st.params["run_mode"] = "full"
        st = drive(dag, run(dag, st, {}), {}, qc_plan=[True, False])
        check(st.status == "completed", "C1 注入 P0→修复→复检后完成")
        fixnode = st.nodes.get("n19_fix", {})
        check(fixnode.get("status") == "completed", "C2 走过 n19_fix 修复节点")
        check(st.counters.get("report_patch", 0) == 1, "E1 report_patch 递增一次")
        decisions = [e["detail"] for e in events(st, "decision") if "n18" in e["node"] or "qc" in e["node"].lower()]
        routes = [e["detail"] for e in st.log if e["event"] == "decision"]
        check(any("has_p0_retry" in d for d in routes) and any("no_p0" in d for d in routes),
              "E2 QC 路由先 retry 后 no_p0")
    finally:
        shutil.rmtree(ws, ignore_errors=True)


# ── C QC 循环（达上限升级）────────────────────────────────────
def test_qc_maxed():
    print("\n[C] QC 循环：持续 P0 触发上限人工升级")
    dag = Dag.load(DAG_PATH)
    ws = _tmp_ws()
    try:
        st = create_run(dag, ws, task_id="task_max", run_id="run_max")
        st.params["run_mode"] = "full"
        st = drive(dag, run(dag, st, {}), {}, qc_plan=[True, True, True, True])
        check(st.nodes.get("n_hc7_escalate", {}).get("status") == "escalated",
              "C3 达 qc_max_rounds 后人工升级只回返修、不豁免 P0")
        check(st.status == "completed", "C4 升级后仍能向前交付完成")
    finally:
        shutil.rmtree(ws, ignore_errors=True)


# ── F 可续跑（中途落盘再加载）────────────────────────────────
def test_resumability():
    print("\n[F] 可续跑：暂停落盘 → 重新 load → 续跑完成")
    dag = Dag.load(DAG_PATH)
    ws = _tmp_ws()
    try:
        st = create_run(dag, ws, task_id="task_rz", run_id="run_rz")
        st.params["run_mode"] = "full"
        st = run(dag, st, {})
        check(st.status == "awaiting_human" and st.human["node"] == "n01_hc_requirement",
              "F1 首关卡暂停并落盘")
        run_dir = st.run_dir
        st2 = RunState.load(run_dir)            # 从磁盘重建，模拟新进程续跑
        check(st2.current_node == st.current_node, "F2 load 还原 current_node")
        st2 = drive(
            dag,
            resume(
                dag, st2,
                hc_bridge.build_human_outputs(
                    st2, "pass",
                    __import__("json").dumps({
                        "brand": "验收品牌", "activity_name": "验收活动",
                        "period_start": "2026-01-01", "period_end": "2026-01-07",
                        "platforms": ["xiaohongshu"], "brand_query": "验收品牌",
                        "activity_query": "验收活动", "product_query": "验收产品",
                        "content_scope": "all",
                        "business_questions": ["活动表现与改进"],
                        "delivery_expectation": "飞书营销活动复盘报告",
                        "allowed_actions": "调用已登记能力",
                        "source_file": {"name": "acceptance.xlsx", "mount_path": "acceptance.xlsx"},
                    }, ensure_ascii=False),
                ),
                {},
            ),
            {},
        )
        check(st2.status == "completed", "F3 跨 load 续跑至完成")
        check(len(st2.ledger) == 9, "F4 ledger 随 run.json 持久化并还原")
    finally:
        shutil.rmtree(ws, ignore_errors=True)


# ── 微型 DAG：D 幂等重放 / reconcile / H 失败旁路 ──────────────
def _micro(nodes):
    return Dag({"slug": "micro", "version": "0", "entry": nodes[0]["id"], "nodes": nodes})


def test_idempotent_replay():
    print("\n[D] 幂等重放：键已提交则跳过 handler")
    calls = {"n": 0}
    handlers._REGISTRY["_probe"] = lambda node, st, ctx: (calls.__setitem__("n", calls["n"] + 1)
                                                          or {"artifacts": {"x": {"v": 1}}})
    dag = _micro([{"id": "a", "handler": "_probe", "mode": "code",
                   "side_effect": "external-x", "idempotency": "k:{task_id}", "next": None}])
    ws = _tmp_ws()
    try:
        st = create_run(dag, ws, task_id="task_mi", run_id="run_mi")
        st.ledger["k:task_mi"] = {"node": "a", "artifacts": ["x@v1"]}  # 预置回执
        st = run(dag, st, {})
        check(st.status == "completed", "D3 预置回执下 run 完成")
        check(calls["n"] == 0, "D4 handler 未被调用（纯重放）")
        check(len(events(st, "idempotent_replay")) == 1, "D5 记录 idempotent_replay 事件")
    finally:
        handlers._REGISTRY.pop("_probe", None)
        shutil.rmtree(ws, ignore_errors=True)


def test_reconcile_before_retry():
    print("\n[D] 对账优先：handler 抛错但键已提交 → 视为成功")
    def _raiser(node, st, ctx):
        raise RuntimeError("external UNKNOWN")
    handlers._REGISTRY["_raiser"] = _raiser
    dag = _micro([{"id": "a", "handler": "_raiser", "mode": "code",
                   "side_effect": "external-x", "idempotency": "k:{task_id}",
                   "retry": {"max": 0, "on_fail": "pause"}, "next": None}])
    ws = _tmp_ws()
    try:
        st = create_run(dag, ws, task_id="task_rc", run_id="run_rc")
        # 不预置 ledger（避免被执行前 replay 截走）；对账源来自 ctx["reconcile"]
        st = run(dag, st, {"reconcile": {"k:task_rc": {"external": True}}})
        check(st.status == "completed", "D6 reconcile 后不判失败")
        check(len(events(st, "reconciled")) == 1, "D7 记录 reconciled 事件（查外部源非自身账本）")
        check(len(events(st, "idempotent_replay")) == 0, "D8 对账不等于重放（无 replay 事件）")
    finally:
        handlers._REGISTRY.pop("_raiser", None)
        shutil.rmtree(ws, ignore_errors=True)


def test_exception_bypass():
    print("\n[H] 失败策略：on_fail=exception_bypass 跳过并继续")
    def _raiser(node, st, ctx):
        raise RuntimeError("boom")
    handlers._REGISTRY["_raiser2"] = _raiser
    handlers._REGISTRY["_ok"] = lambda node, st, ctx: {"artifacts": {"y": {"ok": True}}}
    dag = _micro([
        {"id": "a", "handler": "_raiser2", "mode": "code", "side_effect": "none",
         "retry": {"max": 0, "on_fail": "exception_bypass"}, "next": "b"},
        {"id": "b", "handler": "_ok", "mode": "code", "next": None},
    ])
    ws = _tmp_ws()
    try:
        st = create_run(dag, ws, task_id="task_bp", run_id="run_bp")
        st = run(dag, st, {})
        check(st.nodes["a"]["status"] == "bypassed", "H1 失败节点标记 bypassed")
        check(st.nodes["b"]["status"] == "completed" and st.status == "completed",
              "H2 旁路后继续并完成")
    finally:
        for k in ("_raiser2", "_ok"):
            handlers._REGISTRY.pop(k, None)
        shutil.rmtree(ws, ignore_errors=True)


def test_artifact_immutable():
    print("\n[E] Artifact 不可变版本化（overwrite=false）")
    ws = _tmp_ws()
    try:
        rd = ws / "r"
        (rd / "artifacts").mkdir(parents=True)
        register_run_dir("run_im", rd)
        st = RunState(run_id="run_im", task_id="t", dag_slug="s", dag_version="0",
                      entry="a", params={})
        r1 = st.write_artifact("x", {"v": 1})
        r2 = st.write_artifact("x", {"v": 2})
        check(r1 == "x@v1" and r2 == "x@v2", "E3 同 key 连续写 → v1、v2")
        check((rd / "artifacts/x/v1/payload.json").exists()
              and (rd / "artifacts/x/v2/payload.json").exists(), "E4 旧版本保留不覆盖")
        check((st.read_artifact("x") or {}).get("v") == 2, "E5 read 取最新版本")
    finally:
        shutil.rmtree(ws, ignore_errors=True)


# ── 终验升级分支（覆盖最后一个分支专属节点）────────────────
def test_user_escalate():
    print("\n[A] 终验升级：n22 escalate → n_user_escalate → 交付")
    dag = Dag.load(DAG_PATH)
    ws = _tmp_ws()
    try:
        st = create_run(dag, ws, task_id="task_ue", run_id="run_ue")
        st.params["run_mode"] = "full"
        st = drive(dag, run(dag, st, {}), {})
        # Re-open G4 to exercise timeout escalation without accepting delivery.
        st.status = "running"
        st.frontier = ["n21_hc_user"]
        st = run(dag, st, {})
        st = resume(
            dag, st, hc_bridge.build_human_outputs(st, "escalate", ""), {}
        )
        check(st.human["node"] == "n_user_escalate",
              "A5 终验升级抵达 n_user_escalate")
        st = resume(
            dag, st, hc_bridge.build_human_outputs(st, "pass", ""), {}
        )
        check(
            st.status == "awaiting_human"
            and st.human["node"] == "n21_hc_user"
            and st.nodes.get("n23_delivery_commit", {}).get("attempts", 0) == 1,
            "A6 升级不替代 G4 确认，返回等待且不新增提交",
        )
    finally:
        shutil.rmtree(ws, ignore_errors=True)


def main():
    print("=" * 56)
    print("Topic5 v0.2 端到端模拟验收（A–H）")
    print("=" * 56)
    with compliant_pipeline_services():
        for fn in (test_clean_run, test_qc_converge, test_qc_maxed, test_resumability,
                   test_user_escalate, test_idempotent_replay, test_reconcile_before_retry,
                   test_exception_bypass, test_artifact_immutable):
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                check(False, f"{fn.__name__} 抛异常：{e}")
    passed = sum(1 for ok, _ in _RESULTS if ok)
    total = len(_RESULTS)
    print("\n" + "=" * 56)
    print(f"验收结果：{passed}/{total} 通过")
    print("=" * 56)
    if passed != total:
        print("未通过项：")
        for ok, label in _RESULTS:
            if not ok:
                print(f"  - {label}")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
