"""CLI：启动 / 续跑 / 查看 本地 Topic5 pipeline run —— 薄 Agent 的编排入口。

薄 Agent + Durable Workflow 契约（Gateway 据此驱动）：
  start   先 create_run 再 run；遇人工关卡暂停并落 hc_pending.json、打印 HC JSON。
  resume  注入用户结构化决定续跑：--hc {kind} --decision {pass|reject|remark} [--note ...]
          决定经 hc_bridge 翻译为当前暂停节点所需 human_outputs（无 schema 变更）。
  status  查看当前状态与待办关卡。

用法：
  python run_pipeline.py start  --dag <dag.yaml> --workspace <ws> --task <task_id> [--run <run_id>]
  python run_pipeline.py resume --dag <dag.yaml> --workspace <ws> --task <task_id> --run <run_id> \
         --hc G3 --decision pass [--note "看起来没问题"]
  python run_pipeline.py status --workspace <ws> --task <task_id> --run <run_id>

remark 无内容时只记录、不续跑（等待用户后续消息指令，project_memory 硬约束）。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import hc_bridge
from engine import Dag, create_run, resume, retry_failed, run
from state import RunState, register_run_dir


def _run_dir(ws: Path, task: str, run_id: str) -> Path:
    return ws / "tasks" / task / "runs" / run_id


def _on_pause(st: RunState) -> None:
    """暂停在人工关卡：落 hc_pending.json 并打印 HC JSON（供薄 Agent 产卡结束 turn）。"""
    payload = hc_bridge.write_hc_pending(st, st.run_dir)
    print("HC_PENDING " + json.dumps(payload, ensure_ascii=False))


def _print_state(st: RunState) -> None:
    done = sum(1 for n in st.nodes.values() if n["status"] == "completed")
    print(f"\n[run {st.run_id}] status={st.status} node={st.current_node} "
          f"completed={done}/{len(st.nodes)} qc_rounds={st.counters.get('qc_rounds', 0)}")
    if st.status == "awaiting_human" and st.human:
        hc_kind = hc_bridge.hc_kind_for_node(st.human["node"])
        print(f"  ⏸ 人工关卡 {hc_kind}（{st.human['node']}）：{st.human['prompt']}  "
              f"需提供: {st.human['expect']}")
        print(f"    续跑: python run_pipeline.py resume --dag <dag> --workspace <ws> "
              f"--task {st.task_id} --run {st.run_id} --hc {hc_kind} --decision pass")
    if st.status == "completed":
        print(f"  ✅ 交付 handoff: {st.artifacts.get('handoff', {}).get('path')}")
        handoff = st.read_artifact("handoff") or {}
        delivery = st.read_artifact("delivery_record") or {}
        feishu_doc = st.read_artifact("feishu_doc") or {}
        final_result = {
            "status": "completed",
            "online_url": delivery.get("url") or feishu_doc.get("url"),
            "report_version": handoff.get("report_version"),
            "revision_id": handoff.get("bound_revision_id"),
            "live_revision_id": handoff.get("live_revision_id"),
            "handoff_validated": bool(handoff.get("validated")),
            "terminal_accepted": bool(handoff.get("terminal_accepted")),
            "degraded": bool(handoff.get("degraded")),
            "report_ref": delivery.get("report_ref"),
            "delivery_ref": delivery.get("delivery_ref"),
            "validation_ref": delivery.get("validation_ref"),
            "user_acceptance_ref": delivery.get("user_acceptance_ref"),
            "run_id": st.run_id,
            "task_id": st.task_id,
        }
        print("FINAL_RESULT " + json.dumps(
            final_result, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "cmd",
        choices=["start", "resume", "retry", "status"],
    )
    ap.add_argument("--dag")
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--task", required=True)
    ap.add_argument("--run")
    ap.add_argument("--mode", choices=["demo", "full"], default="full",
                    help="start：仅记录请求模式；demo 只由 Gateway 用于 G1 表单预填，流水线标准相同")
    ap.add_argument("--hc", help="resume：人工关卡种类（G1/G2/G3/G4/HC7/N10/...）")
    ap.add_argument("--decision", choices=["pass", "reject", "remark"],
                    help="resume：用户决定")
    ap.add_argument("--note", default="", help="resume：附注（remark 必带内容才续跑）")
    args = ap.parse_args()

    ws = Path(args.workspace)

    if args.cmd == "start":
        dag = Dag.load(Path(args.dag))
        st = create_run(dag, ws, args.task, args.run)
        st.params["run_mode"] = args.mode
        st.save()
        st = run(dag, st)
        if st.status == "awaiting_human":
            _on_pause(st)
        _print_state(st)
        return

    rd = _run_dir(ws, args.task, args.run)
    register_run_dir(args.run, rd)
    st = RunState.load(rd)

    if args.cmd == "status":
        if st.status == "awaiting_human":
            _on_pause(st)
        _print_state(st)
        return

    if args.cmd == "retry":
        dag = Dag.load(Path(args.dag))
        st = retry_failed(dag, st)
        if st.status == "awaiting_human":
            _on_pause(st)
        _print_state(st)
        return

    # resume：把用户决定翻译成当前暂停节点的 human_outputs。
    dag = Dag.load(Path(args.dag))
    if st.status != "awaiting_human":
        st = resume(dag, st)
        _print_state(st)
        return

    decision = args.decision or "pass"
    outs = hc_bridge.build_human_outputs(
        st,
        decision,
        args.note,
        requested_hc=args.hc,
    )
    if outs is None:
        # remark 无内容：只记录，不续跑（等待后续消息）。
        print(f"REMARK_RECORDED node={st.human['node']} note={args.note!r}（未续跑）")
        _print_state(st)
        return
    hc_bridge.clear_hc_pending(rd)
    st = resume(dag, st, human_outputs=outs)
    if st.status == "awaiting_human":
        _on_pause(st)
    _print_state(st)


if __name__ == "__main__":
    main()
