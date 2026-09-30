#!/usr/bin/env python3
"""Atomically update Topic6 run_config.yaml state.

Parallel annotation workers share one run_config file. Updates are serialized
with a sidecar lock and committed with os.replace so an interrupted write never
leaves partial YAML.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml


def resolve_project_dir(project_dir: str | Path) -> Path:
    path = Path(project_dir)
    return path if path.is_absolute() else Path("/workspace") / path


def _deep_merge(target: dict[str, Any], patch: dict[str, Any]) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = value


def update_run_config(
    project_dir: str | Path,
    patch: dict[str, Any],
    *,
    current_phase: str | None = None,
) -> dict[str, Any]:
    project = resolve_project_dir(project_dir)
    config_path = project / "run_config.yaml"
    if not config_path.exists():
        return {}

    lock_path = project / ".run_config.yaml.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        status = config.setdefault("status", {})
        _deep_merge(status, patch)
        if current_phase is not None:
            status["current_phase"] = current_phase
        status["updated_at"] = datetime.now().astimezone().isoformat()

        temp_path = config_path.with_name(
            f".{config_path.name}.{os.getpid()}.tmp"
        )
        try:
            temp_path.write_text(
                yaml.safe_dump(
                    config,
                    allow_unicode=True,
                    sort_keys=False,
                    width=120,
                ),
                encoding="utf-8",
            )
            os.replace(temp_path, config_path)
        finally:
            temp_path.unlink(missing_ok=True)
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return config


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True)
    parser.add_argument("--phase")
    parser.add_argument(
        "--patch-json",
        default="{}",
        help="JSON object merged into the top-level status block",
    )
    args = parser.parse_args()
    patch = json.loads(args.patch_json)
    if not isinstance(patch, dict):
        raise SystemExit("--patch-json must be a JSON object")
    config = update_run_config(
        args.project_dir,
        patch,
        current_phase=args.phase,
    )
    print(
        json.dumps(
            config.get("status", {}),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
