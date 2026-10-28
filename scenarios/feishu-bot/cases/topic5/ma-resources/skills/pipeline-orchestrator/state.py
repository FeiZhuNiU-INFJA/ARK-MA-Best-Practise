"""Run state & artifact store for the local pipeline orchestrator.

运行期事实的唯一落点：ma-workspaces/.../tasks/{task}/runs/{run}/
- run.json         状态机事实源（当前节点、各节点状态、计数器、人工关卡、日志）
- artifacts/{key}/v{n}/payload.json   不可变 Artifact，内容变更=新版本
- attempts/{node}/{n}/                每次执行的回执/错误
"""
from __future__ import annotations

import json
import hashlib
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


class _Missing(dict):
    """format_map 缺键回退：幂等键模板里尚未就绪的字段渲染为 ∅，不抛错。"""

    def __missing__(self, key):
        return "∅"


@dataclass
class RunState:
    run_id: str
    task_id: str
    dag_slug: str
    dag_version: str
    entry: str
    params: dict[str, Any]
    status: str = "running"                 # running|awaiting_human|completed|failed
    current_node: str | None = None
    nodes: dict[str, dict] = field(default_factory=dict)
    artifacts: dict[str, dict] = field(default_factory=dict)  # key -> {version, path}
    invalidated_artifacts: list[str] = field(default_factory=list)
    counters: dict[str, int] = field(default_factory=dict)
    human: dict | None = None               # pending human gate
    ledger: dict[str, dict] = field(default_factory=dict)  # idempotency_key -> {node, artifacts}
    frontier: list[str] = field(default_factory=list)      # 就绪队列：待调度节点（随 run.json 持久化）
    log: list[dict] = field(default_factory=list)

    # ---- persistence ----
    @property
    def run_dir(self) -> Path:
        return _RUN_DIRS[self.run_id]

    def save(self) -> None:
        p = self.run_dir / "run.json"
        p.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, run_dir: Path) -> "RunState":
        data = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        st = cls(**data)
        _RUN_DIRS[st.run_id] = run_dir
        return st

    # ---- logging ----
    def event(self, node: str, event: str, detail: str = "") -> None:
        self.log.append({"ts": _now(), "node": node, "event": event, "detail": detail})

    # ---- node status ----
    def node_rec(self, node_id: str) -> dict:
        return self.nodes.setdefault(
            node_id, {"status": "pending", "attempts": 0, "artifacts": []}
        )

    # ---- scheduling ----
    def inputs_ready(self, node: dict) -> bool:
        """节点就绪判断：声明的 inputs 全部已在 artifacts 中（空 inputs 视为就绪）。

        这是数据依赖汇合(join)门——并行支路未全部产出前，join 节点留在 frontier 等待。
        注意：QC 环(n17→n18→n19→n17)靠 control 边(next/routes)驱动，不靠本判断，故环安全。
        """
        invalidated = set(self.invalidated_artifacts)
        return all(
            k in self.artifacts and k not in invalidated
            for k in (node.get("inputs") or [])
        )

    # ---- idempotency ----
    def render_idempotency(self, node: dict) -> str | None:
        """渲染节点幂等键；无键或 none 返回 None。缺字段回退 ∅ 不抛错。

        字段来源：task_id/run_id + params（含 n04 冻结的 resource_lock）
        + confirmed_plan.version（若已确认）。
        """
        tmpl = node.get("idempotency")
        if not tmpl or tmpl == "none":
            return None
        fields = {"task_id": self.task_id, "run_id": self.run_id}
        for k, v in self.params.items():
            if isinstance(v, (str, int, float)):
                fields[k] = v
        plan = self.read_artifact("confirmed_plan") or {}
        if isinstance(plan, dict) and "version" in plan:
            fields.setdefault("confirmed_plan_version", plan["version"])
        report = self.read_artifact("report_md") or {}
        if isinstance(report, dict) and report.get("version") is not None:
            fields.setdefault("report_version", report["version"])
        report_ref = self.artifact_ref("report_md")
        if report_ref:
            fields.setdefault("report_artifact_id", report_ref["artifact_id"])
            fields.setdefault("report_artifact_sha256", report_ref.get("sha256"))
        return tmpl.format_map(_Missing(fields))

    # ---- artifact store (immutable, versioned) ----
    def write_artifact(self, key: str, payload: Any) -> str:
        prev = self.artifacts.get(key, {}).get("version", 0)
        version = prev + 1
        adir = self.run_dir / "artifacts" / key / f"v{version}"
        adir.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(
            payload, ensure_ascii=False, indent=2, sort_keys=True
        )
        payload_path = adir / "payload.json"
        payload_path.write_text(serialized, encoding="utf-8")
        ref = f"{key}@v{version}"
        self.artifacts[key] = {
            "version": version,
            "path": str(payload_path),
            "sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
            "ref": ref,
        }
        self.invalidated_artifacts = [
            item for item in self.invalidated_artifacts if item != key
        ]
        return ref

    def read_artifact(self, key: str) -> Any:
        if key in self.invalidated_artifacts:
            return None
        meta = self.artifacts.get(key)
        if not meta:
            return None
        return json.loads(Path(meta["path"]).read_text(encoding="utf-8"))

    def artifact_ref(self, key: str) -> dict[str, Any] | None:
        """Return the immutable identity of the currently selected artifact."""
        if key in self.invalidated_artifacts:
            return None
        meta = self.artifacts.get(key)
        if not meta:
            return None
        return {
            "artifact_id": meta.get("ref") or f"{key}@v{meta['version']}",
            "key": key,
            "version": meta["version"],
            "sha256": meta.get("sha256"),
        }

    def invalidate_artifacts(self, keys: list[str]) -> None:
        """Invalidate active pointers without deleting immutable history."""
        current = set(self.invalidated_artifacts)
        invalidated_refs: set[str] = set()
        for key in keys:
            if key in self.artifacts:
                current.add(key)
                meta = self.artifacts[key]
                invalidated_refs.add(
                    meta.get("ref") or f"{key}@v{meta.get('version')}"
                )
        self.invalidated_artifacts = sorted(current)
        if invalidated_refs:
            def receipt_artifact_ids(receipt: dict) -> set[str]:
                ids: set[str] = set()
                for item in receipt.get("artifacts") or []:
                    if isinstance(item, str):
                        ids.add(item)
                    elif isinstance(item, dict):
                        artifact_id = item.get("artifact_id") or item.get("ref")
                        if artifact_id:
                            ids.add(str(artifact_id))
                return ids

            self.ledger = {
                idem: receipt
                for idem, receipt in self.ledger.items()
                if not invalidated_refs.intersection(receipt_artifact_ids(receipt))
            }

    def record_attempt(self, node_id: str, attempt: int, receipt: dict) -> None:
        adir = self.run_dir / "attempts" / node_id / str(attempt)
        adir.mkdir(parents=True, exist_ok=True)
        (adir / "receipt.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
        )


# run_id -> run_dir（进程内缓存，避免把路径塞进序列化状态）
_RUN_DIRS: dict[str, Path] = {}


def register_run_dir(run_id: str, run_dir: Path) -> None:
    _RUN_DIRS[run_id] = run_dir
