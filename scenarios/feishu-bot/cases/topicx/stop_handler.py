"""Unified stop-task card action handling."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

from ..topic5.pipeline_store import (
    FAILURE_SCOPE_SESSION as TOPIC5_SESSION_FAILURE,
    STATUS_STOPPED as TOPIC5_STOPPED,
    STATUS_PAUSED as TOPIC5_PAUSED,
    STATUS_STOPPING as TOPIC5_STOPPING,
    PipelineStore as Topic5Store,
)
from ..topic5.topic5_progress_card import build_progress_card as build_topic5_card
from ..topic6.pipeline_store import (
    FAILURE_SCOPE_SESSION as TOPIC6_SESSION_FAILURE,
    STATUS_STOPPED as TOPIC6_STOPPED,
    STATUS_STOPPING as TOPIC6_STOPPING,
    PipelineStore as Topic6Store,
)
from ..topic6.topic6_progress_card import build_progress_card as build_topic6_card
from arkagent.session_state import (
    is_session_quiescent,
    is_session_terminal,
    session_status_from_payload,
)
from arkagent.ark import ArkError
from .control_store import ControlStore

log = logging.getLogger("arkagent.topicx.stop")


class StopHandler:
    def __init__(
        self,
        *,
        control: ControlStore,
        topic5_store: Topic5Store,
        topic6_store: Topic6Store,
        ark,
        feishu,
        admin_open_ids: tuple[str, ...],
        runner,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._control = control
        self._topic5_store = topic5_store
        self._topic6_store = topic6_store
        self._ark = ark
        self._feishu = feishu
        self._admins = set(admin_open_ids)
        self._runner = runner
        self._loop = loop
        self._stop_tasks: dict[str, asyncio.Task] = {}

    async def handle(self, action: object) -> bool:
        value = _action_value(action)
        if not value:
            return False
        if value.get("action") == "retry_task":
            return await self._retry_failed(action, value)
        if value.get("action") == "resume_task":
            return await self._resume_paused(action, value)
        if value.get("action") not in {"stop_task", "pause_task"}:
            return False
        scenario = str(value.get("scenario") or "")
        job_id = str(value.get("job_id") or "")
        task = self._control.get_task_by_job(scenario, job_id)
        if task is None:
            return True
        operator = _operator_open_id(action)
        if not self._operator_allowed(task, scenario, operator):
            action_name = "暂停" if scenario == "topic5" else "停止"
            await self._runner.reply(task, f"你没有{action_name}该任务的权限。")
            return True
        if task.status in ("stopped", "done", "failed", "busy"):
            action_name = "暂停" if scenario == "topic5" else "停止"
            await self._runner.reply(
                task, f"该任务已经结束，{action_name}按钮已失效。"
            )
            return True
        if task.status == "stopping":
            action_name = "暂停" if scenario == "topic5" else "停止"
            await self._runner.reply(task, f"任务正在{action_name}，请稍候。")
            return True
        if not self._control.begin_stop(task.task_id, operator):
            await self._runner.reply(task, "任务状态已变化，请刷新卡片后重试。")
            return True

        store, job = self._job(scenario, job_id)
        if job is None:
            self._control.append_event(task.task_id, "stop.failed")
            await self._runner.reply(
                task, "未找到对应业务任务，停止状态已保守保留，请管理员检查。"
            )
            return True
        if not store.mark_stopping(job_id, operator):
            self._control.append_event(task.task_id, "stop.failed")
            await self._runner.reply(
                task, "业务任务状态已变化，停止状态已保守保留，请管理员检查。"
            )
            return True
        # Detach the old SSE consumer before sending interrupt. Otherwise a
        # delayed idle from that stream can arrive after a quick resume and
        # overwrite the resumed Topic5 job as a terminal stopped result.
        self._runner.stop_background_for_job(job_id)
        # Card callbacks are acknowledged by lark-channel immediately. Do not
        # patch the same card in that acknowledgement window: Feishu can report
        # a spurious interaction failure when callback completion races a
        # message update. Run the MA interruption independently and show the
        # stopping card only if it takes long enough to need progress feedback.
        self._schedule_stop(task, store, job, operator)
        return True

    def _schedule_stop(self, task, store, job, operator: str) -> None:
        existing = self._stop_tasks.get(task.task_id)
        if existing is not None and not existing.done():
            return

        async def run() -> None:
            progress = self._loop.create_task(
                self._patch_stopping_after_delay(task, job)
            )
            try:
                await self._interrupt_and_finalize(
                    task, store, job, operator
                )
            except Exception as error:  # noqa: BLE001
                log.exception(
                    "background stop failed task=%s job=%s: %s",
                    task.task_id,
                    job.job_id,
                    error,
                )
                self._control.append_event(
                    task.task_id,
                    "stop.failed",
                    payload={"error": str(error)[:300]},
                )
            finally:
                progress.cancel()
                self._stop_tasks.pop(task.task_id, None)

        self._stop_tasks[task.task_id] = self._loop.create_task(run())

    async def _patch_stopping_after_delay(self, task, job) -> None:
        await asyncio.sleep(3.5)
        current = self._control.get_task(task.task_id)
        if current is not None and current.status == "stopping":
            await self._patch(job, stopping=True)

    async def _resume_paused(self, action: object, value: dict) -> bool:
        if str(value.get("scenario") or "") != "topic5":
            return True
        job_id = str(value.get("job_id") or "")
        task = self._control.get_task_by_job("topic5", job_id)
        job = self._topic5_store.get_job(job_id)
        if task is None or job is None:
            return True
        operator = _operator_open_id(action)
        if not self._operator_allowed(task, "topic5", operator):
            return True
        if task.status != "paused" or job.status != TOPIC5_PAUSED:
            await self._runner.reply(task, "该任务当前不处于暂停状态。")
            return True
        try:
            await self._runner.resume_topic5_job(task, job, "继续")
        except Exception as error:  # noqa: BLE001
            log.exception("topic5 resume failed job=%s: %s", job_id, error)
            await self._runner.reply(
                task, f"当前任务无法继续：{str(error)[:200]}"
            )
        return True

    async def _retry_failed(self, action: object, value: dict) -> bool:
        scenario = str(value.get("scenario") or "")
        if scenario not in {"topic5", "topic6"}:
            return True
        job_id = str(value.get("job_id") or "")
        task = self._control.get_task_by_job(scenario, job_id)
        store, job = self._job(scenario, job_id)
        if task is None or job is None:
            return True
        operator = _operator_open_id(action)
        if not self._operator_allowed(task, scenario, operator):
            await self._runner.reply(task, "你没有重试该任务的权限。")
            return True
        if job.status != "failed":
            await self._runner.reply(task, "该任务当前不处于可重试的失败状态。")
            return True
        if getattr(job, "failure_scope", "") not in ("", "recoverable"):
            await self._runner.reply(task, "业务 Session 已终止，当前任务不能重试。")
            return True
        if not store.claim_retry(job_id):
            await self._runner.reply(task, "任务状态已变化或正在重试，请刷新卡片。")
            return True
        try:
            if scenario == "topic5":
                await self._runner.retry_topic5_job(task, job)
            else:
                await self._runner.retry_topic6_job(task, job)
        except Exception as error:  # noqa: BLE001
            current = store.get_job(job_id)
            if current is not None and current.status != "failed":
                store.mark_failed(
                    job_id,
                    job.last_error or str(error),
                    failure_scope=getattr(job, "failure_scope", "") or "recoverable",
                    retry_strategy=getattr(job, "retry_strategy", ""),
                )
            log.exception("%s retry failed job=%s: %s", scenario, job_id, error)
            await self._runner.reply(
                task, f"当前任务无法继续：{str(error)[:200]}"
            )
        return True

    def _operator_allowed(self, task, scenario: str, operator: str) -> bool:
        """Topic5 cards are collaborative; Topic6 keeps initiator OAuth ownership."""
        if not operator:
            return False
        if scenario == "topic5":
            return True
        return operator in {task.user_open_id, *self._admins}

    async def recover_stopping(self, task) -> None:
        if task.status != "stopping" or not task.business_job_id:
            return
        store, job = self._job(task.scenario, task.business_job_id)
        if job is None:
            self._control.append_event(task.task_id, "stop.recovery_missing_job")
            return
        if await self._finalize_from_session_status(task, store, job):
            return
        await self._patch(job, stopping=True)
        await self._interrupt_and_finalize(
            task,
            store,
            job,
            task.stop_requested_by or "recovered_admin",
            notify_timeout=False,
        )

    async def _interrupt_and_finalize(
        self,
        task,
        store,
        job,
        operator: str,
        *,
        notify_timeout: bool = True,
    ) -> None:
        try:
            boundary = await self._ark.send_interrupt(task.business_session_id)
            result = await self._ark.wait_for_interrupt(
                task.business_session_id, boundary, timeout_ms=30_000
            )
        except Exception as error:
            self._control.append_event(
                task.task_id, "stop.failed", payload={"error": str(error)[:300]}
            )
            action_name = "暂停" if task.scenario == "topic5" else "停止"
            await self._runner.reply(
                task,
                f"中断请求失败，任务仍保持正在{action_name}状态，可稍后重试。",
            )
            return

        if result.terminal == "timeout":
            self._control.append_event(task.task_id, "stop.failed")
            if notify_timeout:
                message = (
                    "尚未确认业务 Session 已暂停，任务仍保持正在暂停状态。"
                    if task.scenario == "topic5"
                    else "尚未确认业务 Session 已停止，任务仍保持正在停止状态。"
                )
                if task.scenario == "topic6":
                    message = (
                        "尚未确认业务 Session 已停止，"
                        "当前仍保守持有 Topic6 任务锁。"
                    )
                await self._runner.reply(task, message)
            return

        if result.terminal == "failed":
            if (
                task.scenario == "topic6"
                and result.error == "session_terminated"
                and await self._delete_topic6_session(task)
            ):
                await self._finalize_stopped(task, store, job, operator)
                return
            await self._finalize_failed(
                task,
                store,
                job,
                result.error or "session_failed_during_stop",
            )
            return

        if not await self._delete_topic6_session(task):
            return
        if task.scenario == "topic5":
            await self._finalize_paused(task, store, job, operator)
        else:
            await self._finalize_stopped(task, store, job, operator)

    async def _finalize_from_session_status(self, task, store, job) -> bool:
        """Reconcile a recovered stop before sending another interrupt.

        An already-idle Session will not emit a new idle event after a repeated
        interrupt. Treat its current state as authoritative so restart recovery
        can converge instead of timing out and notifying the topic forever.
        """
        try:
            session = await self._ark.get_session(task.business_session_id)
        except ArkError as error:
            if task.scenario == "topic6" and error.status_code == 404:
                await self._finalize_stopped(
                    task,
                    store,
                    job,
                    task.stop_requested_by or "recovered_admin",
                )
                return True
            log.warning(
                "stop recovery session lookup failed task=%s session=%s: %s",
                task.task_id,
                task.business_session_id,
                error,
            )
            return False
        except Exception as error:  # noqa: BLE001
            log.warning(
                "stop recovery session lookup failed task=%s session=%s: %s",
                task.task_id,
                task.business_session_id,
                error,
            )
            return False
        status = session_status_from_payload(session)
        if is_session_quiescent(status):
            if not await self._delete_topic6_session(
                task, notify_failure=False
            ):
                return False
            if task.scenario == "topic5":
                await self._finalize_paused(
                    task, store, job,
                    task.stop_requested_by or "recovered_admin",
                )
            else:
                await self._finalize_stopped(
                    task, store, job,
                    task.stop_requested_by or "recovered_admin",
                )
            return True
        if task.scenario == "topic6" and status == "terminated":
            if not await self._delete_topic6_session(
                task, notify_failure=False
            ):
                return False
            await self._finalize_stopped(
                task,
                store,
                job,
                task.stop_requested_by or "recovered_admin",
            )
            return True
        if is_session_terminal(status):
            await self._finalize_failed(
                task, store, job, f"session_{status}_during_stop"
            )
            return True
        return False

    async def _delete_topic6_session(
        self, task, *, notify_failure: bool = True
    ) -> bool:
        """Topic6 stop is irreversible: interrupt to idle, then delete Session."""
        if task.scenario != "topic6":
            return True
        try:
            await self._ark.delete_session(task.business_session_id)
        except ArkError as error:
            if error.status_code == 404:
                return True
            self._control.append_event(
                task.task_id,
                "stop.delete_failed",
                payload={"error": str(error)[:300]},
            )
            if notify_failure:
                await self._runner.reply(
                    task,
                    "MA 已暂停，但永久删除 Session 失败；"
                    "任务仍保持正在停止状态，可稍后重试。",
                )
            return False
        except Exception as error:  # noqa: BLE001
            self._control.append_event(
                task.task_id,
                "stop.delete_failed",
                payload={"error": str(error)[:300]},
            )
            if notify_failure:
                await self._runner.reply(
                    task,
                    "MA 已暂停，但永久删除 Session 失败；"
                    "任务仍保持正在停止状态，可稍后重试。",
                )
            return False
        return True

    async def _finalize_failed(self, task, store, job, error: str) -> None:
        failure_scope = (
            TOPIC5_SESSION_FAILURE
            if task.scenario == "topic5"
            else TOPIC6_SESSION_FAILURE
        )
        store.mark_failed(job.job_id, error, failure_scope=failure_scope)
        self._control.transition(
            task.task_id,
            ("stopping",),
            "failed",
            last_error=error,
            event_type="stop.failed",
        )
        await self._release_topic6(task)
        self._runner.stop_background_for_job(job.job_id)

    async def _finalize_stopped(
        self, task, store, job, operator: str
    ) -> None:
        store.mark_stopped(job.job_id, f"stopped_by:{operator}")
        self._control.transition(
            task.task_id,
            ("stopping",),
            "stopped",
            event_type="stop.confirmed",
        )
        await self._release_topic6(task)
        self._runner.stop_background_for_job(job.job_id)
        stopped_job = store.get_job(job.job_id)
        if stopped_job:
            await self._patch(stopped_job, stopping=False)

    async def _finalize_paused(self, task, store, job, operator: str) -> None:
        store.mark_paused(job.job_id, f"paused_by:{operator}")
        self._control.transition(
            task.task_id,
            ("stopping",),
            "paused",
            event_type="pause.confirmed",
        )
        self._runner.stop_background_for_job(job.job_id)
        paused_job = store.get_job(job.job_id)
        if paused_job:
            await self._patch(paused_job, stopping=False)

    def _job(self, scenario: str, job_id: str):
        store = self._topic5_store if scenario == "topic5" else self._topic6_store
        return store, store.get_job(job_id)

    async def _release_topic6(self, task) -> None:
        if task.scenario != "topic6":
            return
        lease = self._control.get_topic6_lease()
        if lease and lease["owner_task_id"] == task.task_id:
            self._control.release_topic6_if_owner(
                task.task_id, lease["lease_token"], reason="stop_terminal"
            )

    async def _patch(self, job, *, stopping: bool) -> None:
        if not job.progress_card_message_id:
            return
        task = self._control.get_task(job.task_id) if job.task_id else None
        if task is None:
            log.warning(
                "skip stop card patch: control task missing job=%s task=%s",
                job.job_id,
                job.task_id,
            )
            return
        if task.scenario == "topic5":
            status = TOPIC5_STOPPING if stopping else TOPIC5_PAUSED
            card = build_topic5_card(
                job=job,
                status=status,
                elapsed_sec=0,
                tool_lines=[],
                overflow=0,
            )
        else:
            status = TOPIC6_STOPPING if stopping else TOPIC6_STOPPED
            card = build_topic6_card(
                job=job,
                status=status,
                elapsed_sec=0,
                tool_lines=[],
                overflow=0,
            )
        await self._loop.run_in_executor(
            None,
            lambda: self._feishu.patch_interactive_card(
                job.progress_card_message_id, card
            ),
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


def _operator_open_id(action: Any) -> str:
    operator = getattr(action, "operator", None)
    if operator is None and isinstance(action, dict):
        operator = action.get("operator")
    value = getattr(operator, "open_id", "") if operator is not None else ""
    if isinstance(operator, dict):
        value = operator.get("open_id") or value
    return str(value or "")
