"""Pipeline orchestration engine (local, Blueflare-independent).

职责：读机器可读 DAG → 按节点驱动 → 守状态门/人工关卡 → 写 Artifact →
      持久化 run.json → 支持断点续跑与质检循环。

边界：本引擎只做编排与状态；节点业务逻辑在 handlers 里。
      将来接 Blueflare/Ada 时，替换本驱动器，DAG 与 Artifact 模型不变。
"""
from __future__ import annotations

import hashlib
import json
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

import handlers
from handlers import HumanPause
from state import RunState, register_run_dir


class Dag:
    def __init__(self, spec: dict):
        self.spec = spec
        self.nodes = {n["id"]: n for n in spec["nodes"]}
        self.entry = spec["entry"]
        self.slug = spec["slug"]
        self.version = spec["version"]
        self.params = spec.get("params", {})

    @classmethod
    def load(cls, path: Path) -> "Dag":
        return cls(yaml.safe_load(path.read_text(encoding="utf-8")))


def create_run(dag: Dag, workspace: Path, task_id: str, run_id: str | None = None) -> RunState:
    run_id = run_id or f"run_{uuid.uuid4().hex[:8]}"
    run_dir = workspace / "tasks" / task_id / "runs" / run_id
    for sub in ("artifacts", "attempts", "validation", "delivery"):
        (run_dir / sub).mkdir(parents=True, exist_ok=True)
    register_run_dir(run_id, run_dir)
    st = RunState(
        run_id=run_id, task_id=task_id, dag_slug=dag.slug, dag_version=dag.version,
        entry=dag.entry, params=dict(dag.params), current_node=dag.entry,
        frontier=[dag.entry],
    )
    st.event("engine", "run_created", f"dag={dag.slug}@{dag.version}")
    st.save()
    return st


def _advance(st: RunState, nxt) -> None:
    """把节点的 next(单个或列表)追加进 frontier；已在队列中的目标去重。

    去重只防同一 fan-out 把同一 join 目标重复入队(如 n06 与 n11 都指向 n12)；
    不影响 QC 环重入——n17 被 n19 重新指向时它已出队，不会被去重拦截。
    """
    targets = nxt if isinstance(nxt, list) else [nxt]
    for t in targets:
        if t and t not in st.frontier:
            st.frontier.append(t)


def _effect_journal_path(st: RunState, idem_key: str) -> Path:
    digest = hashlib.sha256(idem_key.encode("utf-8")).hexdigest()
    return st.run_dir / "attempts" / "_effects" / f"{digest}.json"


def _load_effect_journal(st: RunState, node_id: str, idem_key: str) -> dict | None:
    path = _effect_journal_path(st, idem_key)
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("node") != node_id or payload.get("idempotency_key") != idem_key:
        raise ValueError(f"effect journal identity mismatch: {path}")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise ValueError(f"effect journal result invalid: {path}")
    return result


def _save_effect_journal(
    st: RunState, node_id: str, idem_key: str, result: dict
) -> None:
    path = _effect_journal_path(st, idem_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(
            {
                "node": node_id,
                "idempotency_key": idem_key,
                "result": result,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    tmp.replace(path)


def _artifact_keys(refs: list) -> set[str]:
    keys = set()
    for ref in refs:
        if isinstance(ref, str):
            key = ref.split("@", 1)[0]
            if key:
                keys.add(key)
            continue
        if not isinstance(ref, dict):
            continue
        key = str(ref.get("key") or "")
        if not key:
            key = str(ref.get("artifact_id") or "").split("@", 1)[0]
        if key:
            keys.add(key)
    return keys


def _result_has_declared_outputs(node: dict, result: dict) -> bool:
    declared = set(node.get("outputs") or [])
    actual = set((result.get("artifacts") or {}).keys())
    return declared.issubset(actual)


def run(dag: Dag, st: RunState, ctx: dict[str, Any] | None = None) -> RunState:
    """排空就绪前沿(frontier)，直到完成、失败或遇人工关卡暂停。

    调度：每轮取 frontier 中【第一个 inputs 就绪】的节点运行=串行确定性。
    并发接缝：把"取第一个就绪"改为"取出全部就绪并发执行"即为真并发，DAG 与 handler 不变。
    """
    ctx = ctx or {}
    if st.status in ("completed", "failed", "awaiting_human"):
        return st
    st.status = "running"

    while st.frontier:
        # 并发接缝 ↓：serial=取第一个就绪；真并发=取出全部就绪一起跑
        idx = next((i for i, nid in enumerate(st.frontier)
                    if st.inputs_ready(dag.nodes[nid])), None)
        if idx is None:
            # frontier 非空却无就绪节点：数据依赖无法满足(良构 DAG 不应发生)，失败旁路由测试覆盖
            st.event("engine", "stuck", f"frontier={st.frontier}")
            st.status = "failed"
            st.save()
            return st
        nid = st.frontier[idx]
        st.current_node = nid
        node = dag.nodes[nid]
        rec = st.node_rec(nid)

        if node.get("checkpoint"):
            # Persist while the selected node is still in frontier. A cold restart
            # can safely re-enter it if the process dies before the next commit.
            st.save()
        st.frontier.pop(idx)

        # 幂等重放：副作用节点键已提交则跳过执行、复用已记产物（partial_rerun 安全）
        idem_key = (st.render_idempotency(node)
                    if node.get("side_effect", "none") != "none" else None)
        if idem_key and idem_key in st.ledger:
            ledger_refs = (st.ledger.get(idem_key) or {}).get("artifacts") or []
            ledger_outputs = _artifact_keys(ledger_refs)
            declared_outputs = set(node.get("outputs") or [])
            # Legacy/reconciled ledgers may intentionally omit artifact refs.
            # Only a non-empty, explicit ref list can prove an output-contract
            # mismatch after a DAG upgrade.
            if not ledger_refs or declared_outputs.issubset(ledger_outputs):
                rec["status"] = "completed"
                st.event(node["id"], "idempotent_replay", idem_key)
                _advance(st, node.get("next"))
                st.save()
                continue
            st.event(
                node["id"],
                "stale_idempotency_contract",
                f"missing_outputs={sorted(declared_outputs - ledger_outputs)}",
            )
            st.ledger.pop(idem_key, None)

        rec["status"] = "in_progress"
        rec["attempts"] += 1
        attempt_no = rec["attempts"]
        retry_cycle_base = int(rec.get("retry_cycle_base_attempts") or 0)
        retry_cycle_attempt = attempt_no - retry_cycle_base
        retry = node.get("retry") or {}
        max_retry = retry.get("max", 0)

        try:
            recovered_result = (
                _load_effect_journal(st, node["id"], idem_key)
                if idem_key else None
            )
            if (
                recovered_result is not None
                and not _result_has_declared_outputs(node, recovered_result)
            ):
                missing = sorted(
                    set(node.get("outputs") or [])
                    - set((recovered_result.get("artifacts") or {}).keys())
                )
                st.event(
                    node["id"],
                    "stale_effect_journal_contract",
                    f"missing_outputs={missing}",
                )
                recovered_result = None
            if recovered_result is not None:
                result = recovered_result
                st.event(node["id"], "effect_journal_replay", idem_key)
            else:
                result = handlers.get(node["handler"])(node, st, ctx)
                if idem_key:
                    _save_effect_journal(st, node["id"], idem_key, result)
        except HumanPause as hp:
            rec["status"] = "awaiting_human"
            st.status = "awaiting_human"
            st.human = {"node": node["id"], "prompt": hp.prompt, "expect": hp.expect}
            st.event(node["id"], "awaiting_human", hp.prompt)
            st.save()
            return st
        except Exception as e:  # noqa: BLE001 — 节点失败统一按 retry 策略处理
            st.record_attempt(node["id"], attempt_no, {"error": str(e)})
            # 外部副作用失败：重试前先对账外部回执(reconcile-before-retry)。
            # sim 下对账源取自 ctx["reconcile"]；接真时替换为查询外部系统真实状态。
            recon = (ctx.get("reconcile") or {}) if idem_key else {}
            if idem_key and idem_key in recon:
                st.ledger[idem_key] = {"node": node["id"], "artifacts": [], "reconciled": True}
                rec["status"] = "completed"
                st.event(node["id"], "reconciled", idem_key)
                _advance(st, node.get("next"))
                st.save()
                continue
            if retry_cycle_attempt <= max_retry:
                rec["status"] = "pending"
                st.frontier.insert(idx, nid)   # 重试：放回原位重新调度
                st.event(node["id"], "retry", f"attempt={attempt_no} err={e}")
                continue
            on_fail = retry.get("on_fail", "pause")
            st.event(node["id"], "failed", f"{e} -> {on_fail}")
            if on_fail == "exception_bypass":
                rec["status"] = "bypassed"
                _advance(st, node.get("next"))
                st.save()
                continue
            rec["status"] = "failed"
            st.status = "failed"
            st.save()
            return st

        st.record_attempt(node["id"], attempt_no, {"ok": True})

        # decision 节点：按命中 route 追加单一目标
        if node.get("mode") == "decision":
            route = result["route"]
            target = node["routes"][route]
            rec["status"] = "completed"
            rec.setdefault("routes", []).append(route)
            st.event(node["id"], "decision", f"{route} -> {target}")
            _advance(st, target)
            st.save()
            continue

        # 普通节点：落 Artifact
        written = []
        for key, payload in (result.get("artifacts") or {}).items():
            ref = st.write_artifact(key, payload)
            rec["artifacts"].append(ref)
            written.append(ref)
        # 幂等记账：副作用提交后登记键→产物，供后续重放/对账（overwrite_artifact=false 下安全）
        if idem_key:
            st.ledger[idem_key] = {"node": node["id"], "artifacts": written}
            st.event(node["id"], "idempotent_commit", idem_key)
        rec["status"] = "completed"
        rec.pop("retry_cycle_base_attempts", None)
        st.event(node["id"], "completed", f"-> {node.get('next')}")
        _advance(st, node.get("next"))

    handoff = st.read_artifact("handoff")
    if "n24_handoff" in dag.nodes and (
        not isinstance(handoff, dict) or not handoff.get("terminal_accepted")
    ):
        st.status = "failed"
        st.event(
            "engine", "terminal_validation_failed",
            "handoff.terminal_accepted != true",
        )
        st.save()
        return st
    st.current_node = None
    st.status = "completed"
    st.event("engine", "run_completed", "")
    st.save()
    return st


def resume(dag: Dag, st: RunState, human_outputs: dict[str, Any] | None = None,
           ctx: dict[str, Any] | None = None) -> RunState:
    """Resume a human gate using explicit outputs and optional rewind control."""
    if st.status != "awaiting_human":
        return run(dag, st, ctx)

    gate = st.human or {}
    node = dag.nodes[gate["node"]]
    outputs = human_outputs or {}
    control = outputs.get("_control") or {}
    missing = [key for key in gate.get("expect", []) if key not in outputs]
    if missing:
        raise ValueError(
            f"{node['id']}: human outputs missing required artifacts: {missing}"
        )
    for key in gate.get("expect", []):
        payload = outputs[key]
        ref = st.write_artifact(key, payload)
        st.node_rec(node["id"])["artifacts"].append(ref)
    for key, payload in outputs.items():
        if key == "_control" or key in gate.get("expect", []):
            continue
        ref = st.write_artifact(key, payload)
        st.node_rec(node["id"])["artifacts"].append(ref)
    invalidate = [
        str(key) for key in (control.get("invalidate_artifacts") or [])
    ]
    if invalidate:
        st.invalidate_artifacts(invalidate)
        st.event(node["id"], "artifacts_invalidated", ",".join(invalidate))
    target = control.get("next", node.get("next"))
    gate_status = str(control.get("gate_status") or "completed")
    st.node_rec(node["id"])["status"] = gate_status
    st.event(node["id"], "human_resumed", f"{gate_status} -> {target}")
    st.human = None
    _advance(st, target)
    st.status = "running"
    st.save()
    return run(dag, st, ctx)


def retry_failed(dag: Dag, st: RunState,
                 ctx: dict[str, Any] | None = None) -> RunState:
    """Explicitly retry only the current failed node with a fresh retry budget."""
    if st.status != "failed":
        raise ValueError(f"run status is {st.status!r}, expected 'failed'")
    node_id = str(st.current_node or "")
    if not node_id or node_id not in dag.nodes:
        raise ValueError("failed run has no valid current_node")
    rec = st.node_rec(node_id)
    if rec.get("status") != "failed":
        raise ValueError(f"current node {node_id} is not failed")
    rec["retry_cycle_base_attempts"] = int(rec.get("attempts") or 0)
    rec["status"] = "pending"
    if node_id not in st.frontier:
        st.frontier.insert(0, node_id)
    st.status = "running"
    st.event(
        node_id,
        "explicit_retry",
        f"previous_attempts={rec['retry_cycle_base_attempts']}",
    )
    st.save()
    return run(dag, st, ctx)


def replace_failed_source(
    st: RunState,
    *,
    source_name: str,
    source_mount_path: str,
) -> RunState:
    """Replace the source pointer for a failed n04 Excel validation only."""
    if st.status != "failed":
        raise ValueError(f"run status is {st.status!r}, expected 'failed'")
    if st.current_node != "n04_run_init":
        raise ValueError("source replacement is only allowed at n04_run_init")
    rec = st.node_rec("n04_run_init")
    if rec.get("status") != "failed":
        raise ValueError("n04_run_init is not failed")
    failure = next(
        (
            str(item.get("detail") or "")
            for item in reversed(st.log)
            if item.get("node") == "n04_run_init"
            and item.get("event") == "failed"
        ),
        "",
    )
    if "excel_source:" not in failure:
        raise ValueError(
            "source replacement requires an excel_source validation failure"
        )

    mount_path = str(source_mount_path or "").strip()
    if (
        not mount_path
        or Path(mount_path).name != mount_path
        or not mount_path.lower().endswith(".xlsx")
    ):
        raise ValueError("source mount path must be a basename ending in .xlsx")
    name = str(source_name or mount_path).strip()
    if not name or any(char in name for char in ("\x00", "\r", "\n")):
        raise ValueError("source name is invalid")

    current = st.read_artifact("confirmed_plan")
    if not isinstance(current, dict) or not current.get("confirmed"):
        raise ValueError("confirmed_plan is missing or not confirmed")
    updated = deepcopy(current)
    updated["source_file"] = {
        "name": name,
        "mount_path": mount_path,
    }
    next_artifact_version = int(
        (st.artifacts.get("confirmed_plan") or {}).get("version") or 0
    ) + 1
    updated["version"] = f"plan-v{next_artifact_version}"
    ref = st.write_artifact("confirmed_plan", updated)
    st.event(
        "n04_run_init",
        "source_replaced",
        f"{ref} mount_path={mount_path}",
    )
    st.save()
    return st
