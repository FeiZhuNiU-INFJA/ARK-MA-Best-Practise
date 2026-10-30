from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from arkagent.ark import InterruptBoundary, InterruptResult
from cases.topic5.pipeline_store import PipelineStore as Topic5Store
from cases.topic5.pipeline_store import PipelineJob as Topic5Job
from cases.topic5.topic5_progress_card import build_progress_card as topic5_card
from cases.topic6.pipeline_store import PipelineStore as Topic6Store
from cases.topic6.pipeline_store import PipelineJob as Topic6Job
from cases.topic6.topic6_progress_card import build_progress_card as topic6_card
from cases.topic6.topic6_runner import Topic6Config, Topic6Runner
from cases.topicx.control_store import ControlStore
from cases.topicx.gateway import _action_value as gateway_action_value
from cases.topicx.stop_handler import StopHandler
from cases.topicx.topicx_runner import TopicxRunner


def _runner_for_tool(store: ControlStore):
    runner = object.__new__(TopicxRunner)
    runner._control = store
    runner._loop = asyncio.get_running_loop()
    dispatched = []
    runner.schedule_dispatch = dispatched.append
    return runner, dispatched


@pytest.mark.asyncio
async def test_topic6_followup_rejects_non_owner_before_session_side_effects():
    runner = object.__new__(TopicxRunner)
    replies = []

    async def reply(_task, text):
        replies.append(text)

    runner.reply = reply
    message = SimpleNamespace(user_open_id="ou-other")
    task = SimpleNamespace(user_open_id="ou-owner", scenario="topic6")

    await runner.handle_followup(message, task)

    assert replies == ["只有任务发起人可以继续操作该任务。"]


@pytest.mark.asyncio
async def test_topic5_followup_allows_other_topic_member_to_chat():
    runner = object.__new__(TopicxRunner)
    calls = []

    class Ark:
        @staticmethod
        async def send_message(session_id, text):
            calls.append((session_id, text))

    class Hitl:
        @staticmethod
        async def handle_remark_message(**_kwargs):
            return False

    runner._ark = Ark()
    runner._topic5_hitl = Hitl()
    message = SimpleNamespace(
        user_open_id="ou-collaborator",
        chat_id="chat",
        thread_id="thread",
        text="补充活动背景",
        resources=[],
    )
    task = SimpleNamespace(
        user_open_id="ou-owner",
        status="running",
        scenario="topic5",
        business_session_id="session-1",
    )

    await runner.handle_followup(message, task)

    assert calls == [("session-1", "补充活动背景")]


@pytest.mark.asyncio
async def test_topic5_file_followup_attaches_and_resumes_production():
    runner = object.__new__(TopicxRunner)
    calls = []
    job = SimpleNamespace(source_file_name="activity.xlsx")

    class Topic5Runner:
        @staticmethod
        async def attach_activity_file(**kwargs):
            calls.append(("attach", kwargs["resource"]))
            return job

        @staticmethod
        async def resume_after_activity_file(attached_job):
            calls.append(("resume", attached_job))

    runner._topic5_runner = Topic5Runner()
    message = SimpleNamespace(
        user_open_id="ou-collaborator",
        chat_id="chat",
        thread_id="thread",
        text="",
        resources=["xlsx-resource"],
    )
    task = SimpleNamespace(
        user_open_id="ou-owner",
        status="wait_hc",
        scenario="topic5",
    )

    await runner.handle_followup(message, task)

    assert calls == [
        ("attach", "xlsx-resource"),
        ("resume", job),
    ]


@pytest.mark.asyncio
async def test_topic5_failed_file_followup_can_repair_source():
    runner = object.__new__(TopicxRunner)
    calls = []
    job = SimpleNamespace(source_file_name="fixed.xlsx")

    class Topic5Runner:
        @staticmethod
        async def attach_activity_file(**kwargs):
            calls.append(("attach", kwargs["resource"]))
            return job

        @staticmethod
        async def resume_after_activity_file(attached_job):
            calls.append(("resume", attached_job))

    runner._topic5_runner = Topic5Runner()
    message = SimpleNamespace(
        user_open_id="ou-collaborator",
        chat_id="chat",
        thread_id="thread",
        text="",
        resources=["fixed-xlsx-resource"],
    )
    task = SimpleNamespace(
        user_open_id="ou-owner",
        status="failed",
        scenario="topic5",
    )

    await runner.handle_followup(message, task)

    assert calls == [
        ("attach", "fixed-xlsx-resource"),
        ("resume", job),
    ]


@pytest.mark.asyncio
async def test_topic5_paused_followup_resumes_same_job_with_message():
    runner = object.__new__(TopicxRunner)
    calls = []
    job = SimpleNamespace(job_id="job-1")

    runner._topic5_job = lambda _task: job

    async def resume(task, resumed_job, message):
        calls.append((task.task_id, resumed_job.job_id, message))

    runner.resume_topic5_job = resume
    message = SimpleNamespace(
        user_open_id="ou-collaborator",
        chat_id="chat",
        thread_id="thread",
        text="补充信息后继续",
        resources=[],
    )
    task = SimpleNamespace(
        task_id="task-1",
        user_open_id="ou-owner",
        status="paused",
        scenario="topic5",
    )

    await runner.handle_followup(message, task)

    assert calls == [("task-1", "job-1", "补充信息后继续")]


@pytest.mark.asyncio
async def test_topic6_authorization_always_resolves_initiator_vault():
    runner = object.__new__(TopicxRunner)
    calls = []
    runner._config = SimpleNamespace(
        topic6=SimpleNamespace(skip_miaoda_oauth=False)
    )

    class UserAuthorization:
        @staticmethod
        async def vault_id(open_id):
            calls.append(open_id)
            return "vault-user"

    runner._user_authorization = UserAuthorization()
    task = SimpleNamespace(user_open_id="ou-owner")

    vault_id = await runner._ensure_user_authorized(task)

    assert vault_id == "vault-user"
    assert calls == ["ou-owner"]


@pytest.mark.asyncio
async def test_topic6_authorization_skip_does_not_read_user_vault():
    runner = object.__new__(TopicxRunner)
    runner._config = SimpleNamespace(
        topic6=SimpleNamespace(skip_miaoda_oauth=True)
    )

    class UserAuthorization:
        @staticmethod
        async def vault_id(_open_id):
            raise AssertionError("skip 模式不应读取用户 Vault")

    runner._user_authorization = UserAuthorization()

    vault_id = await runner._ensure_user_authorized(
        SimpleNamespace(user_open_id="ou-owner")
    )

    assert vault_id == ""


@pytest.mark.asyncio
async def test_topic6_followup_uses_business_session_without_legacy_hitl():
    runner = object.__new__(TopicxRunner)
    calls = []

    class Ark:
        @staticmethod
        async def send_message(session_id, text):
            calls.append((session_id, text))

    runner._ark = Ark()
    message = SimpleNamespace(
        user_open_id="ou-owner",
        text="补充本周重点",
    )
    task = SimpleNamespace(
        user_open_id="ou-owner",
        status="running",
        scenario="topic6",
        business_session_id="session-1",
    )

    await runner.handle_followup(message, task)

    assert calls == [("session-1", "补充本周重点")]


@pytest.mark.asyncio
async def test_router_tool_commits_and_schedules_without_identity_fields(tmp_path):
    store = ControlStore(str(tmp_path / "control.db"))
    task, _ = store.create_or_get_task(
        feishu_event_id="evt",
        chat_id="chat",
        root_message_id="root",
        user_open_id="ou-user",
        request_text="热点报告",
    )
    runner, dispatched = _runner_for_tool(store)
    output, is_error = await runner._handle_router_tool(
        task.task_id,
        "submit_business_intent",
        {"intent": "topic6", "mode": "full", "reason": "热点周报"},
    )
    assert is_error is False
    assert output["task_id"] == task.task_id
    assert dispatched == [task.task_id]


@pytest.mark.asyncio
async def test_router_tool_rejects_model_supplied_identity(tmp_path):
    store = ControlStore(str(tmp_path / "control.db"))
    task, _ = store.create_or_get_task(
        feishu_event_id="evt",
        chat_id="chat",
        root_message_id="root",
        user_open_id="ou-user",
        request_text="复盘报告",
    )
    runner, dispatched = _runner_for_tool(store)
    output, is_error = await runner._handle_router_tool(
        task.task_id,
        "submit_business_intent",
        {
            "intent": "topic5",
            "mode": "full",
            "reason": "复盘",
            "task_id": "model-controlled",
        },
    )
    assert is_error is True
    assert output["error"] == "identity_fields_forbidden"
    assert dispatched == []


def _stop_values(card: dict):
    values = []
    for element in card["body"]["elements"]:
        for behavior in element.get("behaviors", []):
            value = behavior.get("value", {})
            if value.get("action") in {"stop_task", "pause_task"}:
                values.append((value, element))
    return values


def _retry_values(card: dict):
    values = []
    for element in card["body"]["elements"]:
        for behavior in element.get("behaviors", []):
            value = behavior.get("value", {})
            if value.get("action") == "retry_task":
                values.append((value, element))
    return values


def _resume_values(card: dict):
    values = []
    for element in card["body"]["elements"]:
        for behavior in element.get("behaviors", []):
            value = behavior.get("value", {})
            if value.get("action") == "resume_task":
                values.append((value, element))
    return values


def test_topicx_routes_json_string_form_action_value():
    action = {
        "action": {
            "value": (
                '{"scenario":"topic5","job_id":"job-1",'
                '"hc":"G1","event_id":1,"decision":"pass"}'
            )
        }
    }
    assert gateway_action_value(action) == {
        "scenario": "topic5",
        "job_id": "job-1",
        "hc": "G1",
        "event_id": 1,
        "decision": "pass",
    }


@pytest.mark.parametrize(
    ("builder", "job", "scenario"),
    [
        (
            topic5_card,
            Topic5Job("job5", "c", "", "u", "s", "full", "", task_id="task"),
            "topic5",
        ),
        (
            topic6_card,
            Topic6Job("job6", "c", "", "u", "s", "full", "", task_id="task"),
            "topic6",
        ),
    ],
)
def test_running_cards_have_confirmed_stop_button(builder, job, scenario):
    card = builder(
        job=job,
        status="running",
        elapsed_sec=1,
        tool_lines=[],
        overflow=0,
    )
    values = _stop_values(card)
    assert values[0][0]["scenario"] == scenario
    assert values[0][0]["job_id"] == job.job_id
    expected = "确认暂停任务" if scenario == "topic5" else "确认停止任务"
    assert values[0][1]["confirm"]["title"]["content"] == expected


def test_topic5_paused_card_has_resume_button():
    job = Topic5Job(
        "job5", "c", "", "u", "s", "full", "", task_id="task"
    )
    card = topic5_card(
        job=job, status="paused", elapsed_sec=1, tool_lines=[]
    )
    values = _resume_values(card)
    assert values[0][0]["action"] == "resume_task"
    assert values[0][1]["text"]["content"] == "继续任务"
    assert _stop_values(card) == []


@pytest.mark.parametrize(
    ("builder", "job"),
    [
        (
            topic5_card,
            Topic5Job("job5", "c", "", "u", "s", "full", "", task_id="task"),
        ),
        (
            topic6_card,
            Topic6Job("job6", "c", "", "u", "s", "full", "", task_id="task"),
        ),
    ],
)
def test_stopping_and_terminal_cards_have_no_stop_button(builder, job):
    for status in ("stopping", "stopped", "done", "failed"):
        card = builder(
            job=job,
            status=status,
            elapsed_sec=1,
            tool_lines=[],
            overflow=0,
        )
        assert _stop_values(card) == []


def test_topic5_failed_card_has_retry_current_node_button():
    job = Topic5Job(
        "job5", "c", "", "u", "s", "full", "",
        current_phase="n20_delivery_draft", task_id="task",
        retry_strategy="retry_node",
    )
    card = topic5_card(
        job=job, status="failed", elapsed_sec=1, tool_lines=[], error="400",
    )
    values = _retry_values(card)
    assert len(values) == 1
    assert values[0][0]["job_id"] == job.job_id
    assert "重试失败节点" == values[0][1]["text"]["content"]


def test_topic6_retryable_failed_card_has_continue_button():
    job = Topic6Job(
        "job6",
        "c",
        "",
        "u",
        "s",
        "full",
        "",
        current_phase="F",
        task_id="task",
        failure_scope="recoverable",
    )
    card = topic6_card(
        job=job, status="failed", elapsed_sec=1, tool_lines=[], error="invalid HC3"
    )
    values = _retry_values(card)
    assert len(values) == 1
    assert values[0][0]["scenario"] == "topic6"
    assert values[0][0]["job_id"] == job.job_id
    assert values[0][1]["text"]["content"] == "继续当前任务"
    assert card["header"]["title"]["content"].endswith("执行失败，可重试")


@pytest.mark.parametrize("builder,job", [
    (
        topic5_card,
        Topic5Job(
            "job5", "c", "", "u", "s", "full", "",
            task_id="task", failure_scope="session",
        ),
    ),
    (
        topic6_card,
        Topic6Job(
            "job6", "c", "", "u", "s", "full", "",
            task_id="task", failure_scope="session",
        ),
    ),
])
def test_session_terminal_failed_card_has_no_retry_button(builder, job):
    card = builder(
        job=job, status="failed", elapsed_sec=1, tool_lines=[], error="session_failed"
    )
    assert _retry_values(card) == []
    assert card["header"]["title"]["content"].endswith("已终止")


class _StopArk:
    def __init__(
        self,
        terminal: str = "stopped",
        session_status: str = "running",
        delete_error: Exception | None = None,
    ):
        self.terminal = terminal
        self.session_status = session_status
        self.delete_error = delete_error
        self.sessions = []
        self.deleted_sessions = []

    async def get_session(self, session_id):
        return {"id": session_id, "status": self.session_status}

    async def send_interrupt(self, session_id):
        self.sessions.append(session_id)
        return InterruptBoundary(1, frozenset())

    async def wait_for_interrupt(self, session_id, boundary, timeout_ms):
        return InterruptResult(terminal=self.terminal)

    async def delete_session(self, session_id):
        if self.delete_error is not None:
            raise self.delete_error
        self.deleted_sessions.append(session_id)


async def _wait_stop_background(handler, task_id):
    task = handler._stop_tasks.get(task_id)
    if task is not None:
        await task


class _StopRunner:
    def __init__(self):
        self.replies = []
        self.stopped_jobs = []
        self.retried = []
        self.resumed = []

    async def reply(self, task, text):
        self.replies.append(text)

    def stop_background_for_job(self, job_id):
        self.stopped_jobs.append(job_id)

    async def retry_topic5_job(self, task, job):
        self.retried.append(("topic5", task.task_id, job.job_id))

    async def retry_topic6_job(self, task, job):
        self.retried.append(("topic6", task.task_id, job.job_id))

    async def resume_topic5_job(self, task, job, message="继续"):
        self.resumed.append((task.task_id, job.job_id, message))


class _NoopFeishu:
    def patch_interactive_card(self, message_id, card):
        return None


def _stop_fixture(tmp_path, scenario="topic6"):
    control = ControlStore(str(tmp_path / "control.db"))
    topic5 = Topic5Store(str(tmp_path / "topic5.db"))
    topic6 = Topic6Store(str(tmp_path / "topic6.db"))
    task, _ = control.create_or_get_task(
        feishu_event_id="evt-stop",
        chat_id="chat",
        root_message_id="root",
        user_open_id="ou-owner",
        request_text="报告",
    )
    control.commit_route_once(task.task_id, scenario, "full", "")
    control.claim_dispatch(task.task_id)
    control.bind_business_session(task.task_id, "business-session")
    store = topic5 if scenario == "topic5" else topic6
    job = store.create_job(
        chat_id="chat",
        thread_id="",
        user_open_id="ou-owner",
        ma_session_id="business-session",
        mode="full",
        project_dir="",
        task_id=task.task_id,
    )
    control.bind_business_job(task.task_id, job.job_id)
    control.transition(task.task_id, ("starting",), "running")
    if scenario == "topic6":
        claim = control.acquire_topic6(task.task_id)
        control.activate_topic6(
            task.task_id,
            claim.lease_token,
            "business-session",
            job.job_id,
        )
    return control, topic5, topic6, task, job


def _stop_action(scenario, job_id, operator):
    return {
        "action": {
            "value": {
                "action": "stop_task",
                "scenario": scenario,
                "job_id": job_id,
                "card_version": 1,
            }
        },
        "operator": {"open_id": operator},
    }


def _retry_action(scenario, job_id, operator):
    return {
        "action": {
            "value": {
                "action": "retry_task",
                "scenario": scenario,
                "job_id": job_id,
                "card_version": 1,
            }
        },
        "operator": {"open_id": operator},
    }


def _resume_action(job_id, operator):
    return {
        "action": {
            "value": {
                "action": "resume_task",
                "scenario": "topic5",
                "job_id": job_id,
                "card_version": 1,
            }
        },
        "operator": {"open_id": operator},
    }


@pytest.mark.asyncio
async def test_retry_action_routes_topic6_recoverable_failure(tmp_path):
    control, topic5, topic6, task, job = _stop_fixture(tmp_path)
    topic6.mark_failed(job.job_id, "sse_stream_error", failure_scope="recoverable")
    runner = _StopRunner()
    handler = StopHandler(
        control=control,
        topic5_store=topic5,
        topic6_store=topic6,
        ark=_StopArk(),
        feishu=_NoopFeishu(),
        admin_open_ids=(),
        runner=runner,
        loop=asyncio.get_running_loop(),
    )

    assert await handler.handle(_retry_action("topic6", job.job_id, "ou-owner"))
    assert runner.retried == [("topic6", task.task_id, job.job_id)]


@pytest.mark.asyncio
async def test_retry_action_is_claimed_only_once(tmp_path):
    control, topic5, topic6, task, job = _stop_fixture(tmp_path)
    topic6.mark_failed(job.job_id, "sse_stream_error", failure_scope="recoverable")
    runner = _StopRunner()
    handler = StopHandler(
        control=control,
        topic5_store=topic5,
        topic6_store=topic6,
        ark=_StopArk(),
        feishu=_NoopFeishu(),
        admin_open_ids=(),
        runner=runner,
        loop=asyncio.get_running_loop(),
    )

    action = _retry_action("topic6", job.job_id, "ou-owner")
    assert await handler.handle(action)
    assert await handler.handle(action)
    assert runner.retried == [("topic6", task.task_id, job.job_id)]
    assert "不处于可重试" in runner.replies[-1]


@pytest.mark.asyncio
async def test_stop_rejects_non_owner_and_non_admin(tmp_path):
    control, topic5, topic6, task, job = _stop_fixture(tmp_path)
    ark = _StopArk()
    runner = _StopRunner()
    handler = StopHandler(
        control=control,
        topic5_store=topic5,
        topic6_store=topic6,
        ark=ark,
        feishu=_NoopFeishu(),
        admin_open_ids=("ou-admin-1", "ou-admin-2"),
        runner=runner,
        loop=asyncio.get_running_loop(),
    )

    assert await handler.handle(
        _stop_action("topic6", job.job_id, "ou-other")
    )
    assert control.get_task(task.task_id).status == "running"
    assert ark.sessions == []
    assert "没有停止" in runner.replies[-1]


@pytest.mark.asyncio
async def test_topic5_stop_allows_other_topic_member(tmp_path):
    control, topic5, topic6, task, job = _stop_fixture(
        tmp_path, scenario="topic5"
    )
    ark = _StopArk()
    runner = _StopRunner()
    handler = StopHandler(
        control=control,
        topic5_store=topic5,
        topic6_store=topic6,
        ark=ark,
        feishu=_NoopFeishu(),
        admin_open_ids=(),
        runner=runner,
        loop=asyncio.get_running_loop(),
    )

    assert await handler.handle(
        _stop_action("topic5", job.job_id, "ou-collaborator")
    )
    await _wait_stop_background(handler, task.task_id)
    assert control.get_task(task.task_id).status == "paused"
    assert topic5.get_job(job.job_id).status == "paused"
    assert ark.sessions == ["business-session"]
    assert ark.deleted_sessions == []


@pytest.mark.asyncio
async def test_topic5_pause_callback_does_not_wait_for_ma_interrupt(tmp_path):
    control, topic5, topic6, task, job = _stop_fixture(
        tmp_path, scenario="topic5"
    )
    release = asyncio.Event()

    class BlockingArk(_StopArk):
        async def wait_for_interrupt(self, session_id, boundary, timeout_ms):
            await release.wait()
            return InterruptResult(terminal="stopped")

    handler = StopHandler(
        control=control,
        topic5_store=topic5,
        topic6_store=topic6,
        ark=BlockingArk(),
        feishu=_NoopFeishu(),
        admin_open_ids=(),
        runner=_StopRunner(),
        loop=asyncio.get_running_loop(),
    )

    assert await asyncio.wait_for(
        handler.handle(
            _stop_action("topic5", job.job_id, "ou-collaborator")
        ),
        timeout=0.1,
    )
    assert control.get_task(task.task_id).status == "stopping"
    assert handler._runner.stopped_jobs == [job.job_id]
    release.set()
    await _wait_stop_background(handler, task.task_id)
    assert control.get_task(task.task_id).status == "paused"


@pytest.mark.asyncio
async def test_topic5_resume_button_allows_other_topic_member(tmp_path):
    control, topic5, topic6, task, job = _stop_fixture(
        tmp_path, scenario="topic5"
    )
    control.transition(task.task_id, ("running",), "paused")
    topic5.mark_paused(job.job_id)
    runner = _StopRunner()
    handler = StopHandler(
        control=control,
        topic5_store=topic5,
        topic6_store=topic6,
        ark=_StopArk(),
        feishu=_NoopFeishu(),
        admin_open_ids=(),
        runner=runner,
        loop=asyncio.get_running_loop(),
    )

    assert await handler.handle(
        _resume_action(job.job_id, "ou-collaborator")
    )
    assert runner.resumed == [(task.task_id, job.job_id, "继续")]


@pytest.mark.asyncio
async def test_topic5_retry_allows_other_topic_member(tmp_path):
    control, topic5, topic6, task, job = _stop_fixture(
        tmp_path, scenario="topic5"
    )
    topic5.mark_failed(
        job.job_id, "node failed", failure_scope="recoverable"
    )
    runner = _StopRunner()
    handler = StopHandler(
        control=control,
        topic5_store=topic5,
        topic6_store=topic6,
        ark=_StopArk(),
        feishu=_NoopFeishu(),
        admin_open_ids=(),
        runner=runner,
        loop=asyncio.get_running_loop(),
    )

    assert await handler.handle(
        _retry_action("topic5", job.job_id, "ou-collaborator")
    )
    assert runner.retried == [("topic5", task.task_id, job.job_id)]


@pytest.mark.asyncio
async def test_stop_timeout_keeps_topic6_lease_and_stopping_state(tmp_path):
    control, topic5, topic6, task, job = _stop_fixture(tmp_path)
    ark = _StopArk("timeout")
    runner = _StopRunner()
    handler = StopHandler(
        control=control,
        topic5_store=topic5,
        topic6_store=topic6,
        ark=ark,
        feishu=_NoopFeishu(),
        admin_open_ids=("ou-admin-1", "ou-admin-2"),
        runner=runner,
        loop=asyncio.get_running_loop(),
    )

    assert await handler.handle(
        _stop_action("topic6", job.job_id, "ou-admin-1")
    )
    await _wait_stop_background(handler, task.task_id)
    assert control.get_task(task.task_id).status == "stopping"
    assert topic6.get_job(job.job_id).status == "stopping"
    assert control.get_topic6_lease()["state"] == "stopping"


@pytest.mark.asyncio
async def test_owner_stop_confirms_new_idle_result_and_releases_lease(tmp_path):
    control, topic5, topic6, task, job = _stop_fixture(tmp_path)
    ark = _StopArk("stopped")
    runner = _StopRunner()
    handler = StopHandler(
        control=control,
        topic5_store=topic5,
        topic6_store=topic6,
        ark=ark,
        feishu=_NoopFeishu(),
        admin_open_ids=("ou-admin-1", "ou-admin-2"),
        runner=runner,
        loop=asyncio.get_running_loop(),
    )

    assert await handler.handle(
        _stop_action("topic6", job.job_id, "ou-owner")
    )
    await _wait_stop_background(handler, task.task_id)
    assert control.get_task(task.task_id).status == "stopped"
    assert topic6.get_job(job.job_id).status == "stopped"
    assert control.get_topic6_lease() is None
    assert ark.sessions == ["business-session"]
    assert ark.deleted_sessions == ["business-session"]


@pytest.mark.asyncio
async def test_topic6_delete_failure_keeps_stopping_and_lease(tmp_path):
    control, topic5, topic6, task, job = _stop_fixture(tmp_path)
    ark = _StopArk(
        "stopped", delete_error=RuntimeError("delete unavailable")
    )
    runner = _StopRunner()
    handler = StopHandler(
        control=control,
        topic5_store=topic5,
        topic6_store=topic6,
        ark=ark,
        feishu=_NoopFeishu(),
        admin_open_ids=(),
        runner=runner,
        loop=asyncio.get_running_loop(),
    )

    assert await handler.handle(
        _stop_action("topic6", job.job_id, "ou-owner")
    )
    await _wait_stop_background(handler, task.task_id)
    assert control.get_task(task.task_id).status == "stopping"
    assert topic6.get_job(job.job_id).status == "stopping"
    assert control.get_topic6_lease() is not None
    assert "永久删除 Session 失败" in runner.replies[-1]


@pytest.mark.asyncio
async def test_recover_stop_finalizes_already_idle_session_without_retry(
    tmp_path,
):
    control, topic5, topic6, task, job = _stop_fixture(
        tmp_path, scenario="topic5"
    )
    assert control.begin_stop(task.task_id, "ou-owner")
    assert topic5.mark_stopping(job.job_id, "ou-owner")
    task = control.get_task(task.task_id)
    ark = _StopArk("timeout", session_status="idle")
    runner = _StopRunner()
    handler = StopHandler(
        control=control,
        topic5_store=topic5,
        topic6_store=topic6,
        ark=ark,
        feishu=_NoopFeishu(),
        admin_open_ids=(),
        runner=runner,
        loop=asyncio.get_running_loop(),
    )

    await handler.recover_stopping(task)

    assert control.get_task(task.task_id).status == "paused"
    assert topic5.get_job(job.job_id).status == "paused"
    assert ark.sessions == []
    assert runner.replies == []
    assert runner.stopped_jobs == [job.job_id]


@pytest.mark.asyncio
async def test_recover_topic6_idle_stop_deletes_session_before_finalize(
    tmp_path,
):
    control, topic5, topic6, task, job = _stop_fixture(tmp_path)
    assert control.begin_stop(task.task_id, "ou-owner")
    assert topic6.mark_stopping(job.job_id, "ou-owner")
    task = control.get_task(task.task_id)
    ark = _StopArk("timeout", session_status="idle")
    runner = _StopRunner()
    handler = StopHandler(
        control=control,
        topic5_store=topic5,
        topic6_store=topic6,
        ark=ark,
        feishu=_NoopFeishu(),
        admin_open_ids=(),
        runner=runner,
        loop=asyncio.get_running_loop(),
    )

    await handler.recover_stopping(task)

    assert control.get_task(task.task_id).status == "stopped"
    assert topic6.get_job(job.job_id).status == "stopped"
    assert ark.sessions == []
    assert ark.deleted_sessions == ["business-session"]
    assert control.get_topic6_lease() is None


@pytest.mark.asyncio
async def test_recover_stop_timeout_does_not_repeat_topic_reply(tmp_path):
    control, topic5, topic6, task, job = _stop_fixture(
        tmp_path, scenario="topic5"
    )
    assert control.begin_stop(task.task_id, "ou-owner")
    assert topic5.mark_stopping(job.job_id, "ou-owner")
    task = control.get_task(task.task_id)
    ark = _StopArk("timeout", session_status="running")
    runner = _StopRunner()
    handler = StopHandler(
        control=control,
        topic5_store=topic5,
        topic6_store=topic6,
        ark=ark,
        feishu=_NoopFeishu(),
        admin_open_ids=(),
        runner=runner,
        loop=asyncio.get_running_loop(),
    )

    await handler.recover_stopping(task)

    assert control.get_task(task.task_id).status == "stopping"
    assert topic5.get_job(job.job_id).status == "stopping"
    assert ark.sessions == ["business-session"]
    assert runner.replies == []


@pytest.mark.asyncio
async def test_topic5_stop_timeout_does_not_claim_to_hold_topic6_lock(tmp_path):
    control, topic5, topic6, task, job = _stop_fixture(
        tmp_path, scenario="topic5"
    )
    ark = _StopArk("timeout")
    runner = _StopRunner()
    handler = StopHandler(
        control=control,
        topic5_store=topic5,
        topic6_store=topic6,
        ark=ark,
        feishu=_NoopFeishu(),
        admin_open_ids=(),
        runner=runner,
        loop=asyncio.get_running_loop(),
    )

    assert await handler.handle(
        _stop_action("topic5", job.job_id, "ou-owner")
    )
    await _wait_stop_background(handler, task.task_id)
    assert "任务锁" not in runner.replies[-1]
    assert "正在暂停状态" in runner.replies[-1]


class _CreateArk:
    def __init__(self, order):
        self.order = order
        self.call = None

    async def create_session(self, agent_id, environment_id, **kwargs):
        self.call = (agent_id, environment_id, kwargs)
        self.order.append("session")
        return "session-1"


class _CreateFeishu:
    def send_interactive_card_in_thread(self, root_message_id, card):
        return "card-1", "thread-1"


class _NoStreamTopic6Runner(Topic6Runner):
    def _spawn_stream(self, job, first_user_message):
        future = asyncio.get_running_loop().create_future()
        future.set_result(None)
        return future


@pytest.mark.asyncio
async def test_topic6_routed_job_sets_title_env_and_activates_after_job(tmp_path):
    order = []
    ark = _CreateArk(order)
    store = Topic6Store(str(tmp_path / "topic6.db"))

    def activate(task_id, lease_token, session_id, job_id):
        assert store.get_job(job_id) is not None
        order.append("activate")
        return True

    runner = _NoStreamTopic6Runner(
        ark,
        _CreateFeishu(),
        store,
        Topic6Config(
            coordinator_agent_id="agent-topic6",
            environment_id="env-topic6",
            memory_store_id="memory-topic6",
            vault_ids=("base-vault",),
        ),
        loop=asyncio.get_running_loop(),
        lease_validator=lambda task_id, token: (
            order.append("validate") or True
        ),
        lease_activator=activate,
    )

    job = await runner.start_routed_job(
        task_id="task-1",
        lease_token="lease-1",
        chat_id="chat",
        thread_id="",
        user_open_id="ou-owner",
        mode="full",
        user_message="热点周报 full",
        root_message_id="root",
        user_vault_id="user-vault",
        on_session_created=lambda session_id: order.append("bind-session"),
        on_job_created=lambda created: order.append("bind-job"),
    )

    assert order == [
        "validate",
        "session",
        "bind-session",
        "bind-job",
        "activate",
    ]
    assert ark.call[0:2] == ("agent-topic6", "env-topic6")
    assert ark.call[2]["title"] == "topicx/task-1/topic6"
    assert ark.call[2]["env_overrides"]["TOPICX_TASK_ID"] == "task-1"
    assert ark.call[2]["env_overrides"]["FEISHU_USER_OPEN_ID"] == "ou-owner"
    assert ark.call[2]["vault_ids"] == ["base-vault", "user-vault"]
    assert job.task_id == "task-1"
