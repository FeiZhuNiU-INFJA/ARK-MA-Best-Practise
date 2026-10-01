#!/usr/bin/env python3
"""Wait for a DataHub annotation batch without crossing the MA bash limit."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import yaml


TASKS = {
    "c0": ("C0_基础事实", "c0_base"),
    "c3": ("C3_节点标注", "c3_node"),
    "r1": ("R1_平台借势", "r1_platform"),
    "r2": ("R2_商业合作", "r2_commercial"),
    "r3": ("R3_风险预警", "r3_risk"),
    "r4": ("R4_创意借鉴", "r4_creative"),
    "r5": ("R5_消费者行为", "r5_consumer"),
}


def _resolve_project_dir(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else Path("/workspace") / path


def _pid_is_running(path: Path) -> bool:
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return True


def inspect_batch(project: Path, tasks: list[str], run_id: int) -> dict:
    config_path = project / "run_config.yaml"
    config = (
        yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if config_path.exists()
        else {}
    )
    run_status = config.get("status", {})
    states: dict[str, dict] = {}

    for task in tasks:
        subdir, status_key = TASKS[task]
        task_dir = project / "04_标注" / subdir
        completion_path = task_dir / f"{task}_completion_meta.json"
        if completion_path.exists():
            completion = json.loads(completion_path.read_text(encoding="utf-8"))
            if int(completion.get("run_id", -1)) == run_id:
                states[task] = {
                    "status": "done",
                    "rows": int(completion.get("total", 0)),
                    "valid_rate": float(completion.get("valid_rate", 0)),
                }
                continue

        status_block = run_status.get(status_key, {})
        status_run_id = status_block.get("run_id")
        if (
            status_block.get("status") == "failed"
            and (status_run_id is None or int(status_run_id) == run_id)
        ):
            states[task] = {
                "status": "failed",
                "error": str(status_block.get("error", "worker failed")),
            }
            continue

        pid_path = task_dir / f"{task}_worker_r{run_id}.pid"
        states[task] = {
            "status": "running" if _pid_is_running(pid_path) else "missing"
        }

    values = {item["status"] for item in states.values()}
    if "failed" in values or "missing" in values:
        overall = "failed"
    elif values == {"done"}:
        overall = "complete"
    else:
        overall = "running"
    return {"status": overall, "run_id": run_id, "tasks": states}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True)
    parser.add_argument("--tasks", required=True, help="逗号分隔，如 c0,c3")
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--wait-seconds", type=int, default=105)
    parser.add_argument("--interval", type=int, default=5)
    args = parser.parse_args()

    tasks = [item.strip().lower() for item in args.tasks.split(",") if item.strip()]
    unknown = sorted(set(tasks) - set(TASKS))
    if not tasks or unknown:
        raise SystemExit(f"无效 --tasks: {unknown or tasks}")
    wait_seconds = min(max(args.wait_seconds, 0), 110)
    interval = min(max(args.interval, 1), 20)
    project = _resolve_project_dir(args.project_dir)
    deadline = time.monotonic() + wait_seconds

    while True:
        result = inspect_batch(project, tasks, args.run_id)
        if result["status"] != "running" or time.monotonic() >= deadline:
            print(json.dumps(result, ensure_ascii=False))
            return 1 if result["status"] == "failed" else 0
        time.sleep(min(interval, max(0, deadline - time.monotonic())))


if __name__ == "__main__":
    raise SystemExit(main())
