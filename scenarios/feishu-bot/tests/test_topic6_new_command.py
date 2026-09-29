"""topic6 /new 指令行为回归。

覆盖 Topic6Runner.cancel_active_job 三种情况:
1. 无活跃 job → 返回 None,不误改任何行;
2. 有活跃 job(running + 已挂 SSE task)→ task.cancel 被调、job 落到 failed、返回 job;
3. cancel 后 get_active_job_by_session_key 返回 None,允许下一次 start_job。

不测 Feishu 发卡/发消息(_render_and_patch 被 stub 空掉),不启真实 event loop 之外的任务。
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

CASE_DIR = Path(__file__).resolve().parents[1] / "cases" / "topic6"


def _load(module_name: str, file_name: str):
    """把 case 目录塞进 sys.path 后 spec_from_file_location 加载 case 模块。

    与 arkagent.case_registry.load_case 的加载方式一致,复现同一进程内导入语义。
    dataclass 需要模块在 sys.modules 中(会用 __module__ 反查),故 exec 前先注册。
    """
    if str(CASE_DIR) not in sys.path:
        sys.path.insert(0, str(CASE_DIR))
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, CASE_DIR / file_name)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


pipeline_store = _load("pipeline_store", "pipeline_store.py")
topic6_runner = _load("topic6_runner", "topic6_runner.py")

PipelineStore = pipeline_store.PipelineStore
STATUS_FAILED = pipeline_store.STATUS_FAILED
STATUS_STOPPED = pipeline_store.STATUS_STOPPED
Topic6Runner = topic6_runner.Topic6Runner
Topic6RunnerError = topic6_runner.Topic6RunnerError
RunnerConfig = topic6_runner.Topic6Config


class _StubArk:
    pass


class _StubSender:
    pass


def _make_runner(tmp_path: Path, loop: asyncio.AbstractEventLoop) -> tuple[Topic6Runner, PipelineStore]:
    store = PipelineStore(str(tmp_path / "topic6.db"))
    cfg = RunnerConfig(
        coordinator_agent_id="agent-x",
        environment_id="env-x",
        memory_store_id="mem-x",
        vault_ids=(),
    )
    runner = Topic6Runner(_StubArk(), _StubSender(), store, cfg, loop=loop)

    async def _noop(*_args, **_kwargs):
        return None

    runner._render_and_patch = _noop  # type: ignore[assignment]
    return runner, store


@pytest.fixture()
def loop():
    loop = asyncio.new_event_loop()
    try:
        yield loop
    finally:
        loop.close()


def test_cancel_when_no_active_job(loop, tmp_path):
    runner, _store = _make_runner(tmp_path, loop)
    result = loop.run_until_complete(
        runner.cancel_active_job(chat_id="c1", thread_id="", user_open_id="u1")
    )
    assert result is None


def test_cancel_active_job_cancels_task_and_marks_failed(loop, tmp_path):
    runner, store = _make_runner(tmp_path, loop)
    job = store.create_job(
        chat_id="c1",
        thread_id="",
        user_open_id="u1",
        ma_session_id="session-1",
        mode="test",
        project_dir="/workspace/x",
    )

    async def _forever():
        await asyncio.sleep(3600)

    async def _run():
        task = loop.create_task(_forever())
        runner._active_streams[job.ma_session_id] = task
        cancelled = await runner.cancel_active_job(
            chat_id="c1", thread_id="", user_open_id="u1"
        )
        # 让被 cancel 的 task 真正走完 CancelledError,避免 pytest 报 pending。
        try:
            await task
        except asyncio.CancelledError:
            pass
        return cancelled, task

    cancelled, task = loop.run_until_complete(_run())
    assert cancelled is not None
    assert cancelled.job_id == job.job_id
    assert task.cancelled()
    # 落库状态 = failed,原因 = cancelled_by_user
    reloaded = store.get_job(job.job_id)
    assert reloaded.status == STATUS_FAILED
    assert reloaded.last_error == "cancelled_by_user"
    # active_streams 已清引用
    assert job.ma_session_id not in runner._active_streams


def test_cancel_frees_session_key_for_next_start(loop, tmp_path):
    runner, store = _make_runner(tmp_path, loop)
    store.create_job(
        chat_id="c1",
        thread_id="",
        user_open_id="u1",
        ma_session_id="session-1",
        mode="test",
        project_dir="/workspace/x",
    )
    loop.run_until_complete(
        runner.cancel_active_job(chat_id="c1", thread_id="", user_open_id="u1")
    )
    assert store.get_active_job_by_session_key("c1", "", "u1") is None


def test_global_active_job_blocks_another_user_and_chat(loop, tmp_path):
    runner, store = _make_runner(tmp_path, loop)
    existing = store.create_job(
        chat_id="c1",
        thread_id="thread-1",
        user_open_id="u1",
        ma_session_id="session-1",
        mode="demo",
        project_dir="/workspace/x",
    )
    store.update_phase(existing.job_id, "C2")

    with pytest.raises(
        Topic6RunnerError,
        match=(
            r"当前已有热点周报任务运行中"
            r"（mode=demo，phase=C2）。请等待前一个任务结束后再发起。"
        ),
    ):
        loop.run_until_complete(
            runner.start_job(
                chat_id="another-chat",
                thread_id="thread-2",
                user_open_id="u2",
                mode="test",
                user_message="热点周报 test",
            )
        )


def test_global_active_job_is_released_after_terminal_status(tmp_path):
    store = PipelineStore(str(tmp_path / "topic6.db"))
    job = store.create_job(
        chat_id="c1",
        thread_id="",
        user_open_id="u1",
        ma_session_id="session-1",
        mode="test",
        project_dir="/workspace/x",
    )

    assert store.get_active_job().job_id == job.job_id
    store.mark_done(job.job_id)
    assert store.get_active_job() is None


def test_start_job_serializes_simultaneous_triggers(loop, tmp_path):
    runner, _store = _make_runner(tmp_path, loop)
    active_calls = 0
    max_active_calls = 0

    async def fake_start_job_locked(**kwargs):
        nonlocal active_calls, max_active_calls
        active_calls += 1
        max_active_calls = max(max_active_calls, active_calls)
        await asyncio.sleep(0.01)
        active_calls -= 1
        return kwargs

    runner._start_job_locked = fake_start_job_locked  # type: ignore[method-assign]

    async def run_both():
        return await asyncio.gather(
            runner.start_job(
                chat_id="c1",
                thread_id="",
                user_open_id="u1",
                mode="demo",
                user_message="热点周报 demo",
            ),
            runner.start_job(
                chat_id="c1",
                thread_id="",
                user_open_id="u2",
                mode="demo",
                user_message="热点周报 demo",
            ),
        )

    loop.run_until_complete(run_both())
    assert max_active_calls == 1


def test_backend_user_interrupt_marks_job_stopped(loop, tmp_path):
    runner, store = _make_runner(tmp_path, loop)
    job = store.create_job(
        chat_id="c1",
        thread_id="",
        user_open_id="u1",
        ma_session_id="session-1",
        mode="test",
        project_dir="/workspace/x",
    )

    async def _events():
        yield {"type": "user.interrupt"}
        yield {
            "type": "session.status_idle",
            "stop_reason": {"type": "end_turn"},
        }

    loop.run_until_complete(runner._consume_events(job.job_id, _events()))

    reloaded = store.get_job(job.job_id)
    assert reloaded.status == STATUS_STOPPED
    assert reloaded.last_error == "用户在方舟后台手动停止了 Session"


def test_idle_without_hc_or_final_url_is_not_marked_done(loop, tmp_path):
    runner, store = _make_runner(tmp_path, loop)
    job = store.create_job(
        chat_id="c1",
        thread_id="",
        user_open_id="u1",
        ma_session_id="session-1",
        mode="demo",
        project_dir="/workspace/x",
    )

    loop.run_until_complete(runner._handle_idle(job.job_id, []))

    reloaded = store.get_job(job.job_id)
    assert reloaded.status == STATUS_STOPPED
    assert reloaded.last_error == "Session 已结束，但未产生 HC 卡点或最终发布 URL"
