"""case_registry + GatewayLock + case-aware paths 单测。"""
from __future__ import annotations

import multiprocessing
import os
import time

import pytest

from arkagent.case_registry import GatewayLock, list_cases, load_case
from arkagent.paths import get_case_paths, get_feishu_lock_path


def test_list_cases_finds_bundled_scenarios():
    names = list_cases()
    assert "topic6" in names
    assert "digital-employee" in names


def test_load_case_topic6_exposes_required_symbols():
    case = load_case("topic6")
    assert hasattr(case.config_module, "load_case_config")
    assert hasattr(case.gateway_module, "build_gateway")


def test_load_case_rejects_unknown_name():
    with pytest.raises(RuntimeError, match="未知 case"):
        load_case("does-not-exist")


def test_get_case_paths_separates_state(tmp_path):
    env = {"ARKAGENT_HOME": str(tmp_path)}
    p = get_case_paths("topic6", env)
    assert p.case_dir == str(tmp_path / "cases" / "topic6")
    assert p.config_path == str(tmp_path / "cases" / "topic6" / "config.env")
    assert p.database_path == str(tmp_path / "cases" / "topic6" / "gateway.db")


def test_gateway_lock_is_exclusive(tmp_path):
    lock_path = str(tmp_path / "gateway.lock")
    with GatewayLock(lock_path):
        with pytest.raises(RuntimeError, match="已有另一个 arkagent gateway"):
            with GatewayLock(lock_path):
                pass


def test_gateway_lock_released_on_exit(tmp_path):
    lock_path = str(tmp_path / "gateway.lock")
    with GatewayLock(lock_path):
        pass
    with GatewayLock(lock_path):
        pass  # 再次抢占应成功


def _hold_lock(path: str, hold_seconds: float, ready_file: str) -> None:
    with GatewayLock(path):
        open(ready_file, "w").close()
        time.sleep(hold_seconds)


def test_gateway_lock_blocks_across_processes(tmp_path):
    """跨进程也要互斥:fcntl.flock 在同一进程内不同 fd 之间已经能互斥,再验一次多进程。"""
    lock_path = str(tmp_path / "gateway.lock")
    ready = str(tmp_path / "ready")
    ctx = multiprocessing.get_context("fork")
    proc = ctx.Process(target=_hold_lock, args=(lock_path, 0.5, ready))
    proc.start()
    try:
        while not os.path.exists(ready):
            time.sleep(0.02)
        with pytest.raises(RuntimeError, match="已有另一个 arkagent gateway"):
            with GatewayLock(lock_path):
                pass
    finally:
        proc.join(timeout=2.0)


def test_feishu_lock_path_scoped_by_app_id(tmp_path):
    env = {"ARKAGENT_HOME": str(tmp_path)}
    assert get_feishu_lock_path("cli_abc", env) == str(tmp_path / "feishu.cli_abc.lock")


def test_feishu_lock_path_can_use_separate_directory(tmp_path):
    state_dir = tmp_path / "state"
    lock_dir = tmp_path / "locks"
    env = {"ARKAGENT_HOME": str(state_dir), "ARKAGENT_LOCK_DIR": str(lock_dir)}
    assert get_feishu_lock_path("cli_abc", env) == str(
        lock_dir / "feishu.cli_abc.lock"
    )
    assert get_case_paths("topic6", env).config_path == str(
        state_dir / "cases" / "topic6" / "config.env"
    )


def test_feishu_lock_path_rejects_bad_app_id():
    with pytest.raises(ValueError, match="非法 FEISHU_APP_ID"):
        get_feishu_lock_path("")
    with pytest.raises(ValueError, match="非法 FEISHU_APP_ID"):
        get_feishu_lock_path("../etc")


def test_feishu_locks_isolated_across_app_ids(tmp_path):
    """不同 App ID 的 gateway 应能并存——这正是把锁按 App 分粒度的目的。"""
    env = {"ARKAGENT_HOME": str(tmp_path)}
    a = get_feishu_lock_path("cli_a", env)
    b = get_feishu_lock_path("cli_b", env)
    with GatewayLock(a):
        with GatewayLock(b):
            pass
