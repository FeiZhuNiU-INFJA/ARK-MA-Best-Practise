"""topic6 case 的 Gateway:飞书触发词 → 长任务 Runner + HITL 卡片回调。

只处理 topic6 场景的入站消息与卡片回调;不涉及数字员工的 /new /role /remember 指令。
非触发词消息(以及未鉴权用户)给出用法提示。
"""
from __future__ import annotations

import asyncio
import logging

from arkagent.feishu import IncomingMessage
from arkagent.store import GatewayStore

from pipeline_store import (
    STATUS_RUNNING,
    STATUS_WAIT_HC,
    PipelineJob,
    PipelineStore,
)
from topic6_hitl import Topic6Hitl, Topic6HitlDeps
from topic6_runner import (
    Topic6Config as RunnerConfig,
    Topic6Runner,
    Topic6RunnerError,
    normalize_user_text,
    parse_trigger,
)
from topic6_user_oauth import (
    FeishuOAuth,
    Topic6UserAuthorization,
    UserAuthorizationRequired,
)

from config import Topic6Config

log = logging.getLogger("arkagent.case.topic6")
TOKEN_KEEPALIVE_INTERVAL_S = 60.0


class Topic6Gateway:
    """飞书 gateway-like 对象:实现 accept + on_card_action。"""

    def __init__(
        self,
        config: Topic6Config,
        store: GatewayStore,
        runner: Topic6Runner,
        hitl: Topic6Hitl,
        user_authorization: Topic6UserAuthorization,
        reply,
        loop: asyncio.AbstractEventLoop,
        pipeline_store: PipelineStore | None = None,
    ) -> None:
        self._config = config
        self._store = store
        self._runner = runner
        self._hitl = hitl
        self._user_authorization = user_authorization
        self._reply = reply
        self._loop = loop
        self._pipeline_store = pipeline_store
        self._authorized = set(config.authorized_open_ids)
        self._token_keepalive_tasks: dict[str, asyncio.Task] = {}

    def accept(self, message: IncomingMessage) -> bool:
        if not _should_handle_message(message):
            return False
        if not self._store.claim_event(message.event_id):
            return False
        self._loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(self._process(message))
        )
        return True

    def on_card_action(self, action: object) -> bool:
        self._loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(self._hitl.handle_card_action(action))
        )
        return True

    async def _process(self, message: IncomingMessage) -> None:
        try:
            if self._authorized and message.user_open_id not in self._authorized:
                await self._reply(message.chat_id, "当前用户未授权。请联系管理员把你的 open_id 加入白名单。")
                self._store.complete_event(message.event_id, "completed")
                return

            text = normalize_user_text(message.text, message.mentioned_bot)

            if text == "/new":
                cancelled = await self._runner.cancel_active_job(
                    chat_id=message.chat_id,
                    thread_id=message.thread_id,
                    user_open_id=message.user_open_id,
                )
                if cancelled is None:
                    await self._reply(
                        message.chat_id,
                        "[/new] 收到。当前会话没有活跃任务,可发送「热点周报 test」「热点周报 demo」或「热点周报 full」开新一轮。",
                    )
                else:
                    self._stop_token_keepalive(cancelled.job_id)
                    await self._reply(
                        message.chat_id,
                        f"[/new] 已取消当前任务 job_id={cancelled.job_id}(状态置为 failed:cancelled_by_user)。可再发触发词开新一轮。",
                    )
                self._store.complete_event(message.event_id, "completed")
                return

            if await self._hitl.handle_remark_message(
                chat_id=message.chat_id,
                thread_id=message.thread_id,
                user_open_id=message.user_open_id,
                text=text,
            ):
                self._store.complete_event(message.event_id, "completed")
                return

            mode = parse_trigger(text)
            if mode is None:
                await self._reply(
                    message.chat_id,
                    "当前 Bot 仅启用 topic6 场景。发送「热点周报 test」(500 条标注校准)、「热点周报 demo」(50 条出报告)或「热点周报 full」(全量)触发;发送 /new 可取消当前任务。",
                )
                self._store.complete_event(message.event_id, "completed")
                return

            try:
                user_vault_id = await self._user_authorization.vault_id(
                    message.user_open_id
                )
                job = await self._runner.start_job(
                    chat_id=message.chat_id,
                    thread_id=message.thread_id,
                    user_open_id=message.user_open_id,
                    mode=mode,
                    user_message=text,
                    user_vault_id=user_vault_id,
                )
                self._start_token_keepalive(job, user_vault_id)
            except (Topic6RunnerError, UserAuthorizationRequired) as error:
                await self._reply(message.chat_id, str(error))
            self._store.complete_event(message.event_id, "completed")
        except Exception as error:  # noqa: BLE001
            self._store.complete_event(message.event_id, "failed")
            reason = str(error)
            await self._reply(message.chat_id, f"执行失败:{reason[:240]}")

    def _start_token_keepalive(
        self, job: PipelineJob, expected_vault_id: str
    ) -> None:
        if self._pipeline_store is None or not expected_vault_id:
            return
        self._stop_token_keepalive(job.job_id)
        loop = self._loop or asyncio.get_running_loop()
        task = loop.create_task(
            self._keep_user_token_alive(job, expected_vault_id)
        )
        self._token_keepalive_tasks[job.job_id] = task
        task.add_done_callback(
            lambda completed, job_id=job.job_id: self._token_keepalive_done(
                job_id, completed
            )
        )

    def _stop_token_keepalive(self, job_id: str) -> None:
        task = self._token_keepalive_tasks.pop(job_id, None)
        if task is not None and not task.done():
            task.cancel()

    def _token_keepalive_done(
        self, job_id: str, task: asyncio.Task
    ) -> None:
        if self._token_keepalive_tasks.get(job_id) is task:
            self._token_keepalive_tasks.pop(job_id, None)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            log.error(
                "topic6 token keepalive stopped unexpectedly job=%s: %s",
                job_id,
                error,
            )

    async def _keep_user_token_alive(
        self, job: PipelineJob, expected_vault_id: str
    ) -> None:
        """活跃任务期间原地轮换用户 Credential，不改变 Session 或 Vault。"""
        assert self._pipeline_store is not None
        while True:
            current = self._pipeline_store.get_job(job.job_id)
            if current is None or current.status not in (
                STATUS_RUNNING,
                STATUS_WAIT_HC,
            ):
                return
            try:
                vault_id = await self._user_authorization.vault_id(
                    job.user_open_id
                )
                if vault_id != expected_vault_id:
                    log.error(
                        "topic6 token keepalive vault changed job=%s "
                        "session=%s expected=%s actual=%s",
                        job.job_id,
                        job.ma_session_id,
                        expected_vault_id,
                        vault_id,
                    )
                    return
            except Exception as error:  # noqa: BLE001 - 临时刷新失败下轮继续
                log.warning(
                    "topic6 token keepalive refresh failed job=%s "
                    "session=%s: %s",
                    job.job_id,
                    job.ma_session_id,
                    error,
                )
            await asyncio.sleep(TOKEN_KEEPALIVE_INTERVAL_S)


def _should_handle_message(message: IncomingMessage) -> bool:
    if not message.text.strip():
        return False
    return message.chat_type == "p2p" or message.mentioned_bot


def build_gateway(config: Topic6Config, ark, sender, loop: asyncio.AbstractEventLoop):
    """Case 入口:装配 PipelineStore + Runner + HITL,返回 Topic6Gateway。"""
    from arkagent.paths import get_case_paths

    case_paths = get_case_paths("topic6")
    store = GatewayStore(case_paths.database_path)

    pipeline_db = config.pipeline_db_path or None
    pipeline_store = PipelineStore(pipeline_db)
    user_authorization = Topic6UserAuthorization(
        pipeline_store,
        ark,
        FeishuOAuth(config.feishu_app_id, config.feishu_app_secret),
    )
    runner = Topic6Runner(
        ark,
        sender,
        pipeline_store,
        RunnerConfig(
            coordinator_agent_id=config.coordinator_agent_id,
            environment_id=config.environment_id,
            memory_store_id=config.memory_store_id,
            vault_ids=(config.vault_id,) if config.vault_id else (),
        ),
        loop=loop,
    )
    hitl = Topic6Hitl(Topic6HitlDeps(store=pipeline_store, runner=runner))
    runner.bind_card_sender(hitl)

    async def reply(chat_id: str, text: str) -> None:
        await loop.run_in_executor(None, sender.send_to_chat, chat_id, text)

    return Topic6Gateway(
        config,
        store,
        runner,
        hitl,
        user_authorization,
        reply,
        loop,
        pipeline_store=pipeline_store,
    )
