"""Router Session and business Session orchestration for Topicx."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

import httpx

from arkagent.ark import ArkClient
from arkagent.feishu import FeishuSender, IncomingMessage

from ..topic5.pipeline_store import PipelineStore as Topic5PipelineStore
from ..topic5.topic5_hitl import Topic5Hitl
from ..topic5.topic5_runner import Topic5Runner
from ..topic6.artifact_sync import Topic6ArtifactSync
from ..topic6.pipeline_store import PipelineStore as Topic6PipelineStore
from ..topic6.topic6_hitl import Topic6Hitl
from ..topic6.topic6_runner import Topic6Runner
from ..topic6.topic6_user_oauth import (
    Topic6UserAuthorization,
    UserAuthorizationRequired,
)
from .config import TopicxConfig
from .control_store import ControlStore, TaskRun

log = logging.getLogger("arkagent.topicx.runner")
SESSION_RECONCILE_ATTEMPTS = 6
SESSION_RECONCILE_INTERVAL_SEC = 10.0
ROUTER_TIMEOUT_MS = 120_000


class TopicxRunner:
    def __init__(
        self,
        *,
        config: TopicxConfig,
        ark: ArkClient,
        feishu: FeishuSender,
        control: ControlStore,
        topic5_store: Topic5PipelineStore,
        topic5_runner: Topic5Runner,
        topic5_hitl: Topic5Hitl,
        topic6_store: Topic6PipelineStore,
        topic6_runner: Topic6Runner,
        topic6_hitl: Topic6Hitl,
        user_authorization: Topic6UserAuthorization,
        artifact_sync: Optional[Topic6ArtifactSync],
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._config = config
        self._ark = ark
        self._feishu = feishu
        self._control = control
        self._topic5_store = topic5_store
        self._topic5_runner = topic5_runner
        self._topic5_hitl = topic5_hitl
        self._topic6_store = topic6_store
        self._topic6_runner = topic6_runner
        self._topic6_hitl = topic6_hitl
        self._user_authorization = user_authorization
        self._artifact_sync = artifact_sync
        self._loop = loop
        self._dispatch_tasks: dict[str, asyncio.Task] = {}
        self._routing_tasks: dict[str, asyncio.Task] = {}
        self._pending_authorizations: dict[str, asyncio.Task] = {}
        self._token_keepalive_tasks: dict[str, asyncio.Task] = {}

    async def start_root(self, message: IncomingMessage) -> TaskRun:
        task, created = self._control.create_or_get_task(
            feishu_event_id=message.event_id,
            chat_id=message.chat_id,
            root_message_id=message.message_id,
            user_open_id=message.user_open_id,
            request_text=message.text.strip(),
            topic_thread_id=message.thread_id,
        )
        if not created:
            return task
        title = f"topicx/{task.task_id}/router"
        try:
            session_id = await self._create_or_reconcile_session(
                agent_id=self._config.router_agent_id,
                environment_id=self._config.router_environment_id,
                title=title,
            )
        except ReconcileRequired as error:
            self._control.transition(
                task.task_id,
                ("routing",),
                "reconcile_required",
                last_error=str(error),
                event_type="reconcile.required",
            )
            await self.reply(task, "路由会话创建结果暂时无法确认，任务已暂停并等待对账。")
            return self._control.get_task(task.task_id) or task
        except Exception as error:
            self._control.transition(
                task.task_id,
                ("routing",),
                "failed",
                last_error=str(error),
            )
            await self.reply(task, "无法启动意图识别，请稍后重试。")
            return self._control.get_task(task.task_id) or task

        self._control.bind_router_session(task.task_id, session_id)

        await self._run_router(task, session_id)
        return self._control.get_task(task.task_id) or task

    async def _run_router(self, task: TaskRun, session_id: str) -> None:
        async def handle_tool(name: str, arguments: dict) -> tuple[str, bool]:
            output, is_error = await self._handle_router_tool(
                task.task_id, name, arguments
            )
            return json.dumps(output, ensure_ascii=False), is_error

        result = await self._ark.run(
            session_id,
            task.request_text,
            ROUTER_TIMEOUT_MS,
            custom_tool_handler=handle_tool,
        )
        current = self._control.get_task(task.task_id)
        if current and not current.scenario:
            self._control.transition(
                task.task_id,
                ("routing",),
                "failed",
                last_error=result.error or "router_did_not_submit_intent",
            )
            await self.reply(
                task,
                "未能识别为复盘报告或热点周报，请明确说明需要哪一种报告。",
            )

    async def _handle_router_tool(
        self, task_id: str, name: str, arguments: dict
    ) -> tuple[dict, bool]:
        if name != "submit_business_intent":
            return {"accepted": False, "error": "unsupported_tool"}, True
        forbidden = {
            "task_id",
            "session_id",
            "router_session_id",
            "business_session_id",
            "job_id",
            "open_id",
            "chat_id",
            "lease_token",
        }
        if forbidden.intersection(arguments):
            return {"accepted": False, "error": "identity_fields_forbidden"}, True
        try:
            result = self._control.commit_route_once(
                task_id,
                str(arguments.get("intent") or ""),
                str(arguments.get("mode") or ""),
                str(arguments.get("reason") or ""),
            )
        except (KeyError, ValueError) as error:
            return {"accepted": False, "error": str(error)}, True
        if result.get("accepted") and result.get("intent") != "unsupported":
            self.schedule_dispatch(task_id)
        elif result.get("accepted"):
            task = self._control.get_task(task_id)
            if task:
                self._loop.create_task(
                    self.reply(
                        task,
                        "当前请求不属于复盘报告或热点周报，未创建业务任务。",
                    )
                )
        return result, not bool(result.get("accepted"))

    def schedule_dispatch(self, task_id: str) -> None:
        existing = self._dispatch_tasks.get(task_id)
        if existing is not None and not existing.done():
            return
        task = self._loop.create_task(self.dispatch(task_id))
        self._dispatch_tasks[task_id] = task
        task.add_done_callback(
            lambda completed, ident=task_id: self._dispatch_done(ident, completed)
        )

    def schedule_router_recovery(self, task_id: str) -> None:
        existing = self._routing_tasks.get(task_id)
        if existing is not None and not existing.done():
            return
        task = self._loop.create_task(self._recover_router(task_id))
        self._routing_tasks[task_id] = task
        task.add_done_callback(
            lambda completed, ident=task_id: self._routing_done(
                ident, completed
            )
        )

    def _dispatch_done(self, task_id: str, task: asyncio.Task) -> None:
        if self._dispatch_tasks.get(task_id) is task:
            self._dispatch_tasks.pop(task_id, None)
        if task.cancelled():
            return
        error = task.exception()
        if error:
            log.error(
                "topicx dispatch failed task=%s",
                task_id,
                exc_info=(type(error), error, error.__traceback__),
            )

    def _routing_done(self, task_id: str, task: asyncio.Task) -> None:
        if self._routing_tasks.get(task_id) is task:
            self._routing_tasks.pop(task_id, None)
        if task.cancelled():
            return
        error = task.exception()
        if error:
            log.error(
                "topicx router recovery failed task=%s",
                task_id,
                exc_info=(type(error), error, error.__traceback__),
            )

    async def dispatch(self, task_id: str) -> None:
        task = self._control.get_task(task_id)
        if task is None or task.scenario not in ("topic5", "topic6"):
            return
        if task.business_job_id and task.business_session_id:
            if task.status in ("starting", "reconcile_required"):
                if task.scenario == "topic6":
                    lease = self._control.get_topic6_lease()
                    if not lease or lease["owner_task_id"] != task.task_id:
                        return
                    if not self._control.activate_topic6(
                        task.task_id,
                        lease["lease_token"],
                        task.business_session_id,
                        task.business_job_id,
                    ):
                        return
                self._control.transition(
                    task.task_id,
                    ("starting", "reconcile_required"),
                    "running",
                    event_type="business.recovered",
                )
                self._control.clear_request_text(task.task_id)
            return
        if task.scenario == "topic5":
            await self._dispatch_topic5(task)
        else:
            await self._dispatch_topic6(task)

    async def _dispatch_topic5(self, task: TaskRun) -> None:
        if task.status == "routed" and not self._control.claim_dispatch(task.task_id):
            return
        await self.reply(task, "已识别为复盘报告，正在启动业务任务。")
        try:
            job = await self._start_topic5(task)
        except ReconcileRequired as error:
            self._control.transition(
                task.task_id,
                ("starting",),
                "reconcile_required",
                last_error=str(error),
                event_type="reconcile.required",
            )
            await self.reply(
                task,
                "复盘报告会话或任务创建结果暂时无法确认，已等待对账。",
            )
            return
        except Exception as error:
            self._control.transition(
                task.task_id,
                ("starting", "reconcile_required"),
                "failed",
                last_error=str(error),
            )
            await self.reply(task, f"复盘报告启动失败：{str(error)[:160]}")
            return
        self._control.bind_business_session(task.task_id, job.ma_session_id)
        self._control.bind_business_job(task.task_id, job.job_id)
        self._control.transition(task.task_id, ("starting",), "running")
        self._control.clear_request_text(task.task_id)

    async def _start_topic5(self, task: TaskRun):
        try:
            return await self._topic5_runner.start_routed_job(
                task_id=task.task_id,
                chat_id=task.chat_id,
                thread_id=task.topic_thread_id,
                user_open_id=task.user_open_id,
                mode=task.mode,
                user_message=task.request_text,
                root_message_id=task.root_message_id,
                existing_session_id=task.business_session_id,
                on_session_created=lambda session_id: self._control.bind_business_session(
                    task.task_id, session_id
                ),
                on_job_created=lambda job: self._control.bind_business_job(
                    task.task_id, job.job_id
                ),
            )
        except httpx.TimeoutException:
            session_id = await self._reconcile_business_session(
                task, self._config.topic5.coordinator_agent_id, "topic5"
            )
            try:
                return await self._topic5_runner.start_routed_job(
                    task_id=task.task_id,
                    chat_id=task.chat_id,
                    thread_id=task.topic_thread_id,
                    user_open_id=task.user_open_id,
                    mode=task.mode,
                    user_message=task.request_text,
                    root_message_id=task.root_message_id,
                    existing_session_id=session_id,
                    on_session_created=lambda created_id: self._control.bind_business_session(
                        task.task_id, created_id
                    ),
                    on_job_created=lambda job: self._control.bind_business_job(
                        task.task_id, job.job_id
                    ),
                )
            except Exception as error:
                self._raise_reconcile_if_bound(task, error)
                raise
        except Exception as error:
            self._raise_reconcile_if_bound(task, error)
            raise

    async def _dispatch_topic6(self, task: TaskRun) -> None:
        if task.status == "routed":
            if not self._control.claim_dispatch(task.task_id, "authorizing"):
                return
        await self.reply(task, "已识别为热点周报，正在检查用户授权。")
        try:
            vault_id = await self._ensure_user_authorized(task)
        except Exception as error:
            self._control.transition(
                task.task_id,
                ("authorizing",),
                "failed",
                last_error=str(error),
                event_type="oauth.failed",
            )
            await self.reply(task, f"用户授权失败：{str(error)[:160]}")
            return
        self._control.append_event(task.task_id, "oauth.completed")
        self._control.transition(task.task_id, ("authorizing",), "acquiring")
        claim = self._control.acquire_topic6(task.task_id)
        if not claim.acquired:
            self._control.transition(task.task_id, ("acquiring",), "busy")
            await self.reply(task, "当前已有热点周报任务运行中，请等待其结束后重新发起。")
            return
        self._control.transition(task.task_id, ("acquiring",), "starting")
        try:
            job = await self._start_topic6(task, claim.lease_token, vault_id)
        except ReconcileRequired as error:
            self._control.transition(
                task.task_id,
                ("starting",),
                "reconcile_required",
                last_error=str(error),
                event_type="reconcile.required",
            )
            await self.reply(task, "热点周报会话创建结果暂时无法确认，已保守持锁等待对账。")
            return
        except Exception as error:
            self._control.transition(
                task.task_id,
                ("starting",),
                "failed",
                last_error=str(error),
            )
            self._control.release_topic6_if_owner(
                task.task_id, claim.lease_token, reason="session_create_failed"
            )
            await self.reply(task, f"热点周报启动失败：{str(error)[:160]}")
            return
        self._control.bind_business_session(task.task_id, job.ma_session_id)
        self._control.bind_business_job(task.task_id, job.job_id)
        self._control.transition(task.task_id, ("starting",), "running")
        self._control.clear_request_text(task.task_id)
        if vault_id:
            self._start_token_keepalive(job.job_id, task.user_open_id, vault_id)
        if self._artifact_sync is not None:
            self._artifact_sync.start(job, self._loop)

    async def _start_topic6(
        self, task: TaskRun, lease_token: str, vault_id: str
    ):
        kwargs = dict(
            task_id=task.task_id,
            lease_token=lease_token,
            chat_id=task.chat_id,
            thread_id=task.topic_thread_id,
            user_open_id=task.user_open_id,
            mode=task.mode,
            user_message=task.request_text,
            root_message_id=task.root_message_id,
            user_vault_id=vault_id,
            existing_session_id=task.business_session_id,
            on_session_created=lambda session_id: self._control.bind_business_session(
                task.task_id, session_id
            ),
            on_job_created=lambda job: self._control.bind_business_job(
                task.task_id, job.job_id
            ),
        )
        try:
            return await self._topic6_runner.start_routed_job(**kwargs)
        except httpx.TimeoutException:
            session_id = await self._reconcile_business_session(
                task, self._config.topic6.coordinator_agent_id, "topic6"
            )
            kwargs["existing_session_id"] = session_id
            try:
                return await self._topic6_runner.start_routed_job(**kwargs)
            except Exception as error:
                self._raise_reconcile_if_bound(task, error)
                raise
        except Exception as error:
            self._raise_reconcile_if_bound(task, error)
            raise

    def _raise_reconcile_if_bound(
        self, task: TaskRun, error: Exception
    ) -> None:
        current = self._control.get_task(task.task_id)
        if current and (
            current.business_session_id or current.business_job_id
        ):
            raise ReconcileRequired(
                "business Session or Job exists but startup completion is unconfirmed"
            ) from error

    async def _ensure_user_authorized(self, task: TaskRun) -> str:
        if self._config.topic6.skip_miaoda_oauth:
            return ""
        try:
            return await self._user_authorization.vault_id(task.user_open_id)
        except UserAuthorizationRequired:
            pass
        existing = self._pending_authorizations.get(task.user_open_id)
        if existing is not None and not existing.done():
            await self.reply(task, "正在等待你完成之前发送的飞书授权。")
            return await asyncio.shield(existing)

        async def authorize() -> str:
            self._control.append_event(task.task_id, "oauth.started")
            open_id = await self._user_authorization.authorize(
                expected_open_id=task.user_open_id,
                on_device_ready=lambda device: self.reply(
                    task,
                    f"请在 5 分钟内完成飞书授权：\n{device.verification_url}",
                ),
            )
            return await self._user_authorization.vault_id(open_id)

        pending = self._loop.create_task(authorize())
        self._pending_authorizations[task.user_open_id] = pending
        try:
            return await asyncio.wait_for(pending, timeout=300)
        finally:
            if self._pending_authorizations.get(task.user_open_id) is pending:
                self._pending_authorizations.pop(task.user_open_id, None)

    async def handle_followup(self, message: IncomingMessage, task: TaskRun) -> None:
        if (
            task.scenario != "topic5"
            and message.user_open_id != task.user_open_id
        ):
            await self.reply(task, "只有任务发起人可以继续操作该任务。")
            return
        text = message.text.strip()
        if text.lower() in ("/new", "/continue", "/resume", "/stop"):
            await self.reply(task, "请使用任务卡片上的操作按钮。")
            return
        if task.status == "paused" and task.scenario == "topic5":
            if not text and not message.resources:
                await self.reply(task, "任务已暂停；请发送文字消息或点击“继续任务”。")
                return
            await self.resume_topic5_job(task, self._topic5_job(task), text or "继续")
            return
        if task.status in ("stopping", "stopped", "done", "failed", "busy"):
            await self.reply(task, "该任务当前不可继续操作。")
            return
        if task.scenario == "topic5":
            if message.resources:
                job = await self._topic5_runner.attach_activity_file(
                    chat_id=message.chat_id,
                    thread_id=message.thread_id,
                    user_open_id=message.user_open_id,
                    resource=message.resources[0],
                )
                await self._topic5_runner.resume_after_activity_file(job)
                return
            if await self._topic5_hitl.handle_remark_message(
                chat_id=message.chat_id,
                thread_id=message.thread_id,
                user_open_id=message.user_open_id,
                text=text,
            ):
                return
        if task.business_session_id and text:
            await self._ark.send_message(task.business_session_id, text)

    def _topic5_job(self, task: TaskRun):
        job = self._topic5_runner.get_job(task.business_job_id)
        if job is None:
            raise RuntimeError("未找到对应 Topic5 业务任务")
        return job

    async def resume_topic5_job(
        self, task: TaskRun, job, message: str = "继续"
    ) -> None:
        if not self._topic5_runner.claim_paused_resume(job.job_id):
            raise RuntimeError("任务状态已变化，请刷新卡片")
        if not self._control.transition(
            task.task_id, ("paused",), "running", event_type="pause.resumed"
        ):
            self._topic5_runner.restore_paused(
                job.job_id, "resume_control_claim_failed"
            )
            raise RuntimeError("任务状态已变化，请刷新卡片")
        try:
            resumed = await self._topic5_runner.continue_job(
                chat_id=job.chat_id,
                thread_id=job.topic_thread_id or job.thread_id,
                user_open_id=job.user_open_id,
                message=message,
            )
            if resumed is None:
                raise RuntimeError("未找到暂停的 Topic5 任务")
        except Exception:
            current = self._topic5_runner.get_job(job.job_id)
            next_status = (
                "failed"
                if current is not None and current.status == "failed"
                else "paused"
            )
            if next_status == "paused":
                self._topic5_runner.restore_paused(
                    job.job_id, "resume_failed"
                )
            self._control.transition(
                task.task_id, ("running",), next_status,
                last_error=(
                    current.last_error
                    if current is not None and current.status == "failed"
                    else ""
                ),
                event_type="pause.resume_failed",
            )
            raise
        await self.reply(task, "任务已继续执行。")

    async def retry_topic5_job(self, task: TaskRun, job) -> None:
        resumed = await self._topic5_runner.continue_job(
            chat_id=job.chat_id,
            thread_id=job.topic_thread_id or job.thread_id,
            user_open_id=job.user_open_id,
        )
        if resumed is None:
            raise RuntimeError("未找到可重试的 Topic5 任务")
        await self.reply(
            task,
            f"已从失败节点 `{job.current_phase}` 继续执行；"
            "既有产物和审核记录保持不变。",
        )

    async def retry_topic6_job(self, task: TaskRun, job) -> None:
        vault_id = ""
        if not self._config.topic6.skip_miaoda_oauth:
            vault_id = await self._user_authorization.vault_id(task.user_open_id)
        existing_lease = self._control.get_topic6_lease()
        claim = self._control.acquire_topic6(task.task_id)
        if not claim.acquired:
            raise RuntimeError("当前已有其他 Topic6 任务运行，请等待其结束后再重试")
        if not self._control.activate_topic6(
            task.task_id,
            claim.lease_token,
            job.ma_session_id,
            job.job_id,
        ):
            if existing_lease is None:
                self._control.release_topic6_if_owner(
                    task.task_id, claim.lease_token, reason="retry_activate_failed"
                )
            raise RuntimeError("Topic6 任务锁恢复失败，请稍后重试")
        try:
            resumed = await self._topic6_runner.continue_job(
                chat_id=job.chat_id,
                thread_id=job.topic_thread_id or job.thread_id,
                user_open_id=job.user_open_id,
            )
        except Exception:
            if existing_lease is None:
                self._control.release_topic6_if_owner(
                    task.task_id, claim.lease_token, reason="retry_failed"
                )
            raise
        if resumed is None:
            if existing_lease is None:
                self._control.release_topic6_if_owner(
                    task.task_id, claim.lease_token, reason="retry_missing_job"
                )
            raise RuntimeError("未找到可重试的 Topic6 任务")
        if vault_id:
            self._start_token_keepalive(
                resumed.job_id, task.user_open_id, vault_id
            )
        if self._artifact_sync is not None:
            self._artifact_sync.start(resumed, self._loop)
        await self.reply(
            task,
            f"已从阶段 `{job.current_phase}` 继续执行；"
            "既有产物和审核记录保持不变。",
        )

    async def recover(self) -> None:
        tasks = self._control.list_recoverable_tasks()
        for task in tasks:
            if task.status == "routing" or (
                task.status == "reconcile_required" and not task.scenario
            ):
                self.schedule_router_recovery(task.task_id)
            elif task.status in (
                "routed",
                "authorizing",
                "acquiring",
                "starting",
                "reconcile_required",
            ):
                self.schedule_dispatch(task.task_id)
        await asyncio.gather(
            self._topic5_runner.recover_active_jobs(),
            self._topic6_runner.recover_active_jobs(),
            return_exceptions=True,
        )

    async def _recover_router(self, task_id: str) -> None:
        task = self._control.get_task(task_id)
        if (
            task is None
            or task.status not in ("routing", "reconcile_required")
            or task.scenario
        ):
            return
        session_id = task.router_session_id
        if not session_id:
            session_id = await self._find_unique_session(
                self._config.router_agent_id,
                f"topicx/{task.task_id}/router",
            )
            self._control.bind_router_session(task.task_id, session_id)
        self._control.transition(
            task.task_id, ("reconcile_required",), "routing"
        )
        await self._run_router(task, session_id)

    async def reply(self, task: TaskRun, text: str) -> None:
        await self._loop.run_in_executor(
            None, lambda: self._feishu.reply_in_thread(task.root_message_id, text)
        )

    async def _create_or_reconcile_session(
        self, *, agent_id: str, environment_id: str, title: str
    ) -> str:
        try:
            return await self._ark.create_session(
                agent_id, environment_id, title=title
            )
        except httpx.TimeoutException:
            return await self._find_unique_session(agent_id, title)

    async def _reconcile_business_session(
        self, task: TaskRun, agent_id: str, scenario: str
    ) -> str:
        self._control.append_event(task.task_id, "reconcile.started")
        self._control.transition(
            task.task_id, ("starting",), "reconcile_required"
        )
        session_id = await self._find_unique_session(
            agent_id, f"topicx/{task.task_id}/{scenario}"
        )
        self._control.append_event(task.task_id, "reconcile.resolved")
        self._control.transition(
            task.task_id, ("reconcile_required",), "starting"
        )
        return session_id

    async def _find_unique_session(self, agent_id: str, title: str) -> str:
        for attempt in range(SESSION_RECONCILE_ATTEMPTS):
            matches = [
                item
                for item in await self._ark.list_sessions(agent_id, limit=100)
                if item.get("title") == title
            ]
            if len(matches) == 1:
                return str(matches[0]["id"])
            if len(matches) > 1:
                raise ReconcileRequired(
                    f"multiple sessions matched exact title {title}"
                )
            if attempt + 1 < SESSION_RECONCILE_ATTEMPTS:
                await asyncio.sleep(SESSION_RECONCILE_INTERVAL_SEC)
        raise ReconcileRequired(f"no session matched exact title {title}")

    def _start_token_keepalive(
        self, job_id: str, user_open_id: str, expected_vault_id: str
    ) -> None:
        async def keepalive() -> None:
            while True:
                job = self._topic6_store.get_job(job_id)
                if job is None or job.status not in ("running", "wait_hc"):
                    return
                vault_id = await self._user_authorization.vault_id(user_open_id)
                if vault_id != expected_vault_id:
                    log.error("topic6 vault changed job=%s", job_id)
                    return
                await asyncio.sleep(60)

        old = self._token_keepalive_tasks.pop(job_id, None)
        if old and not old.done():
            old.cancel()
        self._token_keepalive_tasks[job_id] = self._loop.create_task(keepalive())

    def stop_background_for_job(self, job_id: str) -> None:
        task = self._token_keepalive_tasks.pop(job_id, None)
        if task and not task.done():
            task.cancel()
        if self._artifact_sync is not None:
            self._artifact_sync.stop(job_id)


class ReconcileRequired(RuntimeError):
    pass
