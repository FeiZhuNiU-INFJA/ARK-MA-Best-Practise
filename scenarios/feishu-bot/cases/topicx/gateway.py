"""Unified Feishu entrypoint for Topic5 and Topic6."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Optional

from arkagent.feishu import IncomingMessage

from ..topic5.pipeline_store import PipelineStore as Topic5PipelineStore
from ..topic5.topic5_hitl import Topic5Hitl, Topic5HitlDeps
from ..topic5.topic5_runner import (
    Topic5Config as Topic5RunnerConfig,
    Topic5Runner,
)
from ..topic6.artifact_sync import Topic6ArtifactSync
from ..topic6.pipeline_store import PipelineStore as Topic6PipelineStore
from ..topic6.topic6_hitl import Topic6Hitl, Topic6HitlDeps
from ..topic6.topic6_runner import (
    Topic6Config as Topic6RunnerConfig,
    Topic6Runner,
)
from ..topic6.topic6_user_oauth import FeishuOAuth, Topic6UserAuthorization
from .config import TopicxConfig
from .control_store import ControlStore
from .stop_handler import StopHandler
from .topicx_runner import TopicxRunner

log = logging.getLogger("arkagent.case.topicx")


class TopicxGateway:
    def __init__(
        self,
        *,
        config: TopicxConfig,
        runner: TopicxRunner,
        control: ControlStore,
        topic5_hitl: Topic5Hitl,
        topic6_hitl: Topic6Hitl,
        stop_handler: StopHandler,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._config = config
        self._runner = runner
        self._control = control
        self._topic5_hitl = topic5_hitl
        self._topic6_hitl = topic6_hitl
        self._stop_handler = stop_handler
        self._loop = loop
        self._authorized = set(config.authorized_open_ids)

    def accept(self, message: IncomingMessage) -> bool:
        task = self._task_for_message(message)
        is_followup = task is not None and bool(
            message.thread_id or message.root_id
        )
        if not is_followup and not (
            message.text.strip()
            and (message.chat_type == "p2p" or message.mentioned_bot)
        ):
            return False
        self._loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(self._process(message, task))
        )
        return True

    def on_card_action(self, action: object) -> bool:
        value = _action_value(action) or {}
        log.info(
            "topicx card action received scenario=%s job=%s hc=%s action=%s",
            value.get("scenario") or "",
            value.get("job_id") or "",
            value.get("hc") or "",
            value.get("action") or "",
        )
        self._loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(self._process_card_action(action))
        )
        return True

    async def recover_active_jobs(self) -> None:
        await self._runner.recover()
        stopping = [
            task
            for task in self._control.list_recoverable_tasks()
            if task.status == "stopping"
        ]
        await asyncio.gather(
            *(
                self._stop_handler.recover_stopping(task)
                for task in stopping
            ),
            return_exceptions=True,
        )

    async def _process(self, message: IncomingMessage, task) -> None:
        if self._authorized and message.user_open_id not in self._authorized:
            await self._loop.run_in_executor(
                None,
                lambda: self._runner._feishu.reply_in_thread(  # noqa: SLF001
                    message.message_id, "当前用户未授权，请联系管理员。"
                ),
            )
            return
        try:
            if task is not None and (message.thread_id or message.root_id):
                await self._runner.handle_followup(message, task)
            else:
                await self._runner.start_root(message)
        except Exception as error:  # noqa: BLE001
            log.exception(
                "topicx message processing failed event=%s", message.event_id
            )
            root = message.root_id or message.message_id
            await self._loop.run_in_executor(
                None,
                lambda: self._runner._feishu.reply_in_thread(  # noqa: SLF001
                    root, f"任务处理失败：{str(error)[:200]}"
                ),
            )

    async def _process_card_action(self, action: object) -> None:
        if await self._stop_handler.handle(action):
            return
        value = _action_value(action)
        scenario = str((value or {}).get("scenario") or "")
        if not scenario:
            job_id = str((value or {}).get("job_id") or "")
            if self._control.get_task_by_job("topic5", job_id):
                scenario = "topic5"
            elif self._control.get_task_by_job("topic6", job_id):
                scenario = "topic6"
        if scenario == "topic5":
            await self._topic5_hitl.handle_card_action(action)
        elif scenario == "topic6":
            await self._topic6_hitl.handle_card_action(action)
        else:
            log.warning(
                "topicx card action ignored: unable to route value=%r", value
            )

    def _task_for_message(self, message: IncomingMessage):
        root = message.root_id or ""
        return self._control.get_task_by_thread(
            message.chat_id,
            root or message.message_id,
            message.thread_id,
        )


def build_gateway(
    config: TopicxConfig, ark, sender, loop: asyncio.AbstractEventLoop
) -> TopicxGateway:
    control = ControlStore(config.control_db_path)
    topic5_store = Topic5PipelineStore(config.topic5.pipeline_db_path or None)
    topic6_store = Topic6PipelineStore(config.topic6.pipeline_db_path or None)

    topic5_runner = Topic5Runner(
        ark,
        sender,
        topic5_store,
        Topic5RunnerConfig(
            coordinator_agent_id=config.topic5.coordinator_agent_id,
            environment_id=config.topic5.environment_id,
            memory_store_id=config.topic5.memory_store_id,
            vault_ids=(config.topic5.vault_id,) if config.topic5.vault_id else (),
        ),
        loop=loop,
    )
    topic5_hitl = Topic5Hitl(
        Topic5HitlDeps(store=topic5_store, runner=topic5_runner)
    )
    topic5_runner.bind_card_sender(topic5_hitl)

    def validate_lease(task_id: str, token: str) -> bool:
        lease = control.get_topic6_lease()
        return bool(
            lease
            and lease["owner_task_id"] == task_id
            and lease["lease_token"] == token
            and lease["state"] in ("acquired", "active")
        )

    def activate_lease(
        task_id: str, token: str, session_id: str, job_id: str
    ) -> bool:
        return control.activate_topic6(
            task_id, token, session_id, job_id
        )

    topic6_runner = Topic6Runner(
        ark,
        sender,
        topic6_store,
        Topic6RunnerConfig(
            coordinator_agent_id=config.topic6.coordinator_agent_id,
            environment_id=config.topic6.environment_id,
            memory_store_id=config.topic6.memory_store_id,
            vault_ids=(config.topic6.vault_id,) if config.topic6.vault_id else (),
        ),
        loop=loop,
        lease_validator=validate_lease,
        lease_activator=activate_lease,
    )
    topic6_hitl = Topic6Hitl(
        Topic6HitlDeps(store=topic6_store, runner=topic6_runner)
    )
    topic6_runner.bind_card_sender(topic6_hitl)
    user_authorization = Topic6UserAuthorization(
        topic6_store,
        ark,
        FeishuOAuth(config.feishu_app_id, config.feishu_app_secret),
    )
    artifact_sync = Topic6ArtifactSync(
        ark,
        Path(config.topic6.artifact_sync_dir),
        topic6_store.get_job,
        poll_interval_sec=config.topic6.artifact_poll_interval_sec,
    )

    runner = TopicxRunner(
        config=config,
        ark=ark,
        feishu=sender,
        control=control,
        topic5_store=topic5_store,
        topic5_runner=topic5_runner,
        topic5_hitl=topic5_hitl,
        topic6_store=topic6_store,
        topic6_runner=topic6_runner,
        topic6_hitl=topic6_hitl,
        user_authorization=user_authorization,
        artifact_sync=artifact_sync,
        loop=loop,
    )

    def sync_business_status(job, status: str) -> None:
        if not job.task_id or status in ("stopping", "stopped"):
            return
        task = control.get_task(job.task_id)
        if task is None:
            return
        if status == "wait_hc":
            control.transition(job.task_id, ("running",), "wait_hc")
        elif status == "running":
            control.transition(job.task_id, ("wait_hc", "failed"), "running")
        elif status == "failed" and getattr(job, "failure_scope", "") in (
            "",
            "recoverable",
        ):
            control.append_event(
                job.task_id,
                "business.retryable_failed",
                payload={"error": job.last_error[:300]},
            )
        elif status in ("done", "failed"):
            transitioned = control.transition(
                job.task_id,
                ("running", "wait_hc", "starting"),
                status,
                last_error=job.last_error if status == "failed" else "",
            )
            if not transitioned:
                return
            runner.stop_background_for_job(job.job_id)
            if task.scenario == "topic6":
                lease = control.get_topic6_lease()
                if lease and lease["owner_task_id"] == job.task_id:
                    control.release_topic6_if_owner(
                        job.task_id,
                        lease["lease_token"],
                        reason=status,
                    )

    topic5_store.set_status_listener(sync_business_status)
    topic6_store.set_status_listener(sync_business_status)

    stop_handler = StopHandler(
        control=control,
        topic5_store=topic5_store,
        topic6_store=topic6_store,
        ark=ark,
        feishu=sender,
        admin_open_ids=config.admin_open_ids,
        runner=runner,
        loop=loop,
    )
    return TopicxGateway(
        config=config,
        runner=runner,
        control=control,
        topic5_hitl=topic5_hitl,
        topic6_hitl=topic6_hitl,
        stop_handler=stop_handler,
        loop=loop,
    )


def _action_value(action: Any) -> Optional[dict]:
    action_obj = getattr(action, "action", None)
    if action_obj is None and isinstance(action, dict):
        action_obj = action.get("action")
    value = getattr(action_obj, "value", None)
    if isinstance(action_obj, dict):
        value = action_obj.get("value", value)
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            value = None
    return value if isinstance(value, dict) else None
