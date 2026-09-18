"""数字员工阿J：群聊话题协作与单聊个人协作的统一入口。

交互规则：
  - 主时间线里 @bot：以该消息为根创建新话题，并创建独立 Session。
  - 话题内普通消息：不触发回复；下一次 @bot 时作为该轮上下文一并送入 Session。
  - 其他主时间线消息、未登记话题里的普通消息：忽略。
  - 每轮只读取当前话题中「上一次 @bot 之后到当前」的窗口，不读取主群或其他话题。

执行模式：
  - serial：客户端按话题串行，上一轮结束后再发送下一轮。
  - native-queue：消息直发方舟，由运行中队列吸收/合并，事件流负责回复。

运行：
  set -a && source ~/.arkagent/config.env && set +a
  python scenarios/feishu-bot/cases/digital-employee/digital_employee.py --execution-mode serial
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import threading
import urllib.request
from dataclasses import replace
from pathlib import Path
from typing import Literal, Optional

from shared import (
    GroupBotConfig,
    GroupConversationKey,
    PreparedAttachment,
    SqliteSessionMap,
    build_lark_session_env,
    build_windowed_input,
    collect_round_resources,
    fetch_feishu_tenant_access_token,
    is_authorized,
    is_reset_command,
    lark_cli_enabled,
    load_group_bot_config,
    message_log_tag,
    multimodal_enabled,
    prepare_attachments,
    setup_logging,
    should_handle,
    update_lark_cli_vault_token,
)
from user_oauth import FeishuOAuth, UserAuthorizationManager
from memory import (  # type: ignore[import-not-found]
    DEFAULT_MEMORY_DB_PATH,
    ScopedMemoryManager,
    SqliteMemoryState,
)

from arkagent.ark import (
    ArkClient,
    ArkError,
    RunResult,
    UserAuthorizationRequired,
    event_custom_tool_call,
    event_error,
    event_requires_action,
    event_text,
    event_user_authorization_required,
    remember_lark_cli_tool_domain,
)
from arkagent.feishu import (
    FeishuSender,
    HistoryMessage,
    IncomingMessage,
    QuotedMessage,
    ResourceRef,
    normalize_history_item,
    start_feishu_gateway,
)
from arkagent.gateway import KeyedQueue

log = logging.getLogger("group_bot.topic_session")

DEFAULT_TOPIC_DB_PATH = str(
    Path(__file__).resolve().parents[4] / "data" / "topic_bot_sessions.db"
)
DEFAULT_NATIVE_TOPIC_DB_PATH = str(
    Path(__file__).resolve().parents[4] / "data" / "topic_bot_native_queue_sessions.db"
)
ExecutionMode = Literal["serial", "native-queue"]

MAX_409_RETRIES = 5
BACKOFF_BASE_S = 2.0
BACKOFF_CAP_S = 15.0


# #region debug-point A-D:report-runtime-evidence
def _debug_report(hypothesis_id: str, location: str, msg: str, data: dict) -> None:
    def _send() -> None:
        try:
            env_path = Path(".dbg/native-queue-missed-replies.env")
            settings = dict(
                line.split("=", 1)
                for line in env_path.read_text().splitlines()
                if "=" in line
            )
            payload = json.dumps(
                {
                    "sessionId": settings.get(
                        "DEBUG_SESSION_ID", "native-queue-missed-replies"
                    ),
                    "runId": "post-fix",
                    "hypothesisId": hypothesis_id,
                    "location": location,
                    "msg": f"[DEBUG] {msg}",
                    "data": data,
                }
            ).encode()
            urllib.request.urlopen(
                urllib.request.Request(
                    settings.get(
                        "DEBUG_SERVER_URL", "http://127.0.0.1:7777/event"
                    ),
                    data=payload,
                    headers={"Content-Type": "application/json"},
                ),
                timeout=1,
            ).read()
        except Exception:
            pass

    threading.Thread(target=_send, daemon=True).start()
# #endregion


def _is_runtime_busy(error: Exception) -> bool:
    if isinstance(error, ArkError) and error.status_code == 409:
        return True
    text = str(error)
    return " 409" in text or "RuntimeBusy" in text


def to_topic_key(message: IncomingMessage) -> GroupConversationKey:
    """返回稳定话题键；话题创建前用当前消息 ID，创建后用 thread_id。"""
    topic_id = ""
    if message.chat_type == "group":
        topic_id = message.thread_id or message.message_id
    return GroupConversationKey(message.tenant_key, message.chat_id, topic_id)


def has_topic_coordinates(message: IncomingMessage) -> bool:
    return bool(message.root_id or message.thread_id)


def select_topic_delta(history: list[HistoryMessage]) -> list[HistoryMessage]:
    """取上一次 @bot 之后的消息；边界消息已在 Session 中，不能重复注入。"""
    usable = [item for item in history if not item.is_from_bot]
    trigger_positions = [index for index, item in enumerate(usable) if item.at_bot]
    if not trigger_positions:
        return usable
    return usable[trigger_positions[-1] + 1:]


class TopicSessionBot:
    """严格按飞书话题隔离，可选择客户端串行或方舟原生队列。"""

    def __init__(
        self,
        config: GroupBotConfig,
        ark: ArkClient,
        sender: FeishuSender,
        loop: asyncio.AbstractEventLoop,
        sessions: Optional[object] = None,
        execution_mode: ExecutionMode = "serial",
        user_auth: Optional[UserAuthorizationManager] = None,
        memory_manager: Optional[ScopedMemoryManager] = None,
        memory_state: Optional[object] = None,
    ) -> None:
        if execution_mode not in ("serial", "native-queue"):
            raise ValueError(f"不支持的执行模式：{execution_mode}")
        self._config = config
        self._ark = ark
        self._sender = sender
        self._loop = loop
        self._execution_mode = execution_mode
        default_db_path = (
            DEFAULT_TOPIC_DB_PATH
            if execution_mode == "serial"
            else DEFAULT_NATIVE_TOPIC_DB_PATH
        )
        db_path = os.environ.get("TOPIC_BOT_DB_PATH", default_db_path)
        sessions_were_injected = sessions is not None
        self._sessions = sessions if sessions_were_injected else SqliteSessionMap(db_path)
        self._queue = KeyedQueue()
        self._create_locks: dict[str, asyncio.Lock] = {}
        self._lark_token_lock = asyncio.Lock()
        self._lark_token_refresh_at = 0.0
        self._consumers: dict[str, asyncio.Task] = {}
        self._pending_reactions: dict[
            str, list[tuple[IncomingMessage, Optional[str]]]
        ] = {}
        # 首轮消息到达时飞书尚未生成 thread_id；首条回复后把新 thread 映射回原运行 key。
        self._thread_aliases: dict[str, GroupConversationKey] = {}
        self._authorization_retries: set[str] = set()
        self._native_tool_domains: dict[str, dict[str, str]] = {}
        self._native_authorizing: set[str] = set()
        self._native_blocked_until_idle: set[str] = set()
        self._native_inputs: dict[tuple[str, str], str] = {}
        self._native_prepared: dict[
            tuple[str, str], list[PreparedAttachment]
        ] = {}
        self._user_auth = user_auth or UserAuthorizationManager(
            self._sessions,
            self._ark,
            FeishuOAuth(config.feishu_app_id, config.feishu_app_secret),
            self._send_authorization_card,
            self._notify_authorization_failure,
        )
        if memory_manager is not None:
            self._memory = memory_manager
        else:
            state = memory_state
            if state is None:
                state = (
                    self._sessions
                    if sessions_were_injected
                    else SqliteMemoryState(
                        os.environ.get(
                            "DIGITAL_EMPLOYEE_MEMORY_DB_PATH",
                            DEFAULT_MEMORY_DB_PATH,
                        )
                    )
                )
            self._memory = ScopedMemoryManager(self._ark, state)

    def accept(self, message: IncomingMessage) -> bool:
        """WS 同步入口：单聊需有明确文本请求；群聊只有 @bot 才触发。"""
        tag = message_log_tag(message)
        if not should_handle(message):
            if message.chat_type == "p2p" and message.content_type != "text":
                log.info(
                    "%s 丢弃：单聊消息没有明确文本请求 type=%s",
                    tag,
                    message.content_type,
                )
            elif message.chat_type == "group" and not message.mentioned_bot:
                log.info("%s 丢弃：群消息未 @bot（保留在话题历史，等下次 @bot 时读取）", tag)
            else:
                log.info("%s 丢弃：空文本消息", tag)
            return False

        public_key = to_topic_key(message)
        key = self._thread_aliases.get(public_key.as_str(), public_key)

        if not self._sessions.claim_event(message.event_id):
            log.info("%s 丢弃：event 已处理过", tag)
            return False

        def _submit() -> None:
            if self._execution_mode == "serial":
                log.info("%s 串行入队，topic_key=%s", tag, key.as_str())
                self._queue.enqueue(
                    key.as_str(), lambda: self._run_serial(message, key)
                )
            else:
                log.info("%s 直投方舟原生队列，topic_key=%s", tag, key.as_str())
                self._loop.create_task(self._run_native(message, key))

        self._loop.call_soon_threadsafe(_submit)
        return True

    async def _run_serial(
        self, message: IncomingMessage, key: GroupConversationKey
    ) -> None:
        try:
            await self._process_serial(message, key)
        except Exception:  # noqa: BLE001 - 单轮失败不能拖垮同话题队列
            log.exception("处理话题消息失败")
            await self._reply(message, "执行失败，请稍后重试；若持续失败，请在当前话题发送 /new。")

    async def _process_serial(
        self, message: IncomingMessage, key: GroupConversationKey
    ) -> None:
        tag = message_log_tag(message)
        if not is_authorized(self._config, message):
            await self._reply(message, "当前用户未授权。请联系管理员把你的 user_id 加入白名单。")
            return

        await self._refresh_lark_cli_token()

        if is_reset_command(message.text):
            self._sessions.reset(key)
            session_id = await self._create_session(key, message)
            log.info("%s 当前话题已切换到新 Session=%s", tag, session_id)
            await self._reply(message, "已重置当前话题的 Session；后续消息仍留在本话题。")
            return

        reaction_id = await self._ack(message)
        try:
            session_id = self._sessions.get(key)
            is_first_turn = session_id is None
            if session_id and not await self._session_has_required_vaults(
                session_id, message
            ):
                self._sessions.reset(key)
                session_id = None
                is_first_turn = True
            if not session_id:
                session_id = await self._create_session(key, message)
                log.info("%s 新话题已建 Session=%s", tag, session_id)

            message, roster, actor_input, prepared = await self._prepare_turn(
                message, include_topic_history=not is_first_turn
            )
            await self._mount_attachments(session_id, prepared)
            log.info("%s 发往 Session=%s，input 长度=%d", tag, session_id, len(actor_input))
            log.debug("%s 完整 input：\n%s", tag, actor_input)
            try:
                result = await self._ark.run(
                    session_id,
                    actor_input,
                    self._config.session_timeout_ms,
                    custom_tool_handler=lambda name, arguments: self._memory.handle_tool(
                        session_id, name, arguments
                    ),
                )
            except ArkError as error:
                if error.status_code != 404:
                    raise
                session_id = await self._create_session(key, message)
                await self._mount_attachments(session_id, prepared)
                result = await self._ark.run(
                    session_id,
                    actor_input,
                    self._config.session_timeout_ms,
                    custom_tool_handler=lambda name, arguments: self._memory.handle_tool(
                        session_id, name, arguments
                    ),
                )

            if result.authorization_required:
                if message.chat_type != "p2p":
                    await self._reply(
                        message,
                        "群聊仅使用 Bot 身份，不能读取成员个人数据；请私聊我后再发起该请求。",
                    )
                    return
                await self._request_user_authorization(
                    message,
                    result.authorization_required,
                    lambda: self._resume_serial(
                        message, key, actor_input, prepared, roster
                    ),
                )
                return
            thread_id = await self._reply(message, _result_to_text(result), roster)
            self._bind_thread_session(key, message, session_id, thread_id)
        finally:
            await self._unack(message, reaction_id)

    async def _run_native(
        self, message: IncomingMessage, key: GroupConversationKey
    ) -> None:
        try:
            if not is_authorized(self._config, message):
                await self._reply(
                    message, "当前用户未授权。请联系管理员把你的 user_id 加入白名单。"
                )
                return

            key_str = key.as_str()
            if key_str in self._native_authorizing:
                retry = getattr(self._user_auth, "retry", None)
                if _is_authorization_retry_request(message.text) and retry:
                    if await retry(message):
                        return
                await self._reply(
                    message,
                    "当前正在等待用户授权。若卡片链接已失效，请发送“重新授权”，我会生成一张新卡片。",
                )
                return

            await self._refresh_lark_cli_token()

            if is_reset_command(message.text):
                self._sessions.reset(key)
                await self._stop_consumer(key)
                await self._clear_native_reactions(key)
                session_id, _ = await self._ensure_native_session(key, message)
                log.info(
                    "%s 当前话题已切换到新 Session=%s",
                    message_log_tag(message),
                    session_id,
                )
                await self._reply(
                    message, "已重置当前话题的 Session；后续消息仍留在本话题。"
                )
                return

            reaction_id = await self._ack(message)
            self._pending_reactions.setdefault(key.as_str(), []).append(
                (message, reaction_id)
            )
            session_id, is_first_turn = await self._ensure_native_session(key, message)
            message, _roster, actor_input, prepared = await self._prepare_turn(
                message, include_topic_history=not is_first_turn
            )
            await self._mount_attachments(session_id, prepared)
            self._native_inputs[(key.as_str(), message.message_id)] = actor_input
            self._native_prepared[(key.as_str(), message.message_id)] = prepared
            await self._send_native(
                key, message, session_id, actor_input, prepared
            )
        except Exception as error:  # noqa: BLE001 - 单条失败不能影响后续触发
            log.exception("原生队列处理话题消息失败")
            await self._reply(message, f"执行失败：{str(error)[:240]}")
            await self._clear_native_reactions(key)

    async def _prepare_turn(
        self,
        message: IncomingMessage,
        *,
        include_topic_history: bool = True,
    ) -> tuple[IncomingMessage, dict, str, list[PreparedAttachment]]:
        """构造本轮输入；新 Session 首轮包含当前消息、话题根与显式引用。"""
        roster = await self._chat_roster(message)
        message = _with_roster_name(message, roster)
        history = await self._topic_history(message) if include_topic_history else []
        history = _with_roster_history_names(history, roster)
        root_context = await self._topic_root_context(message)
        root_context = _with_roster_history_names(root_context, roster)
        topic_delta = select_topic_delta(history)
        quote_chain, quoted_resource = await self._explicit_quote(message)
        resources = collect_round_resources(
            message, history, root_context, selected_history=topic_delta
        )
        if quoted_resource is not None:
            resources.extend(quoted_resource.resources)
        prepared, notices = await self._prepare_attachments(
            message, _dedupe_resources(resources)
        )
        actor_input = build_windowed_input(
            message,
            history=history,
            quote_chain=quote_chain,
            thread_context=root_context,
            prepared=prepared,
            notices=notices,
            selected_history=topic_delta,
        )
        try:
            always_apply_context = await self._memory.always_apply_context(message)
        except Exception as error:  # noqa: BLE001 - 记忆读取失败不应阻断正常回复
            log.warning("读取群共享约定失败，本轮继续处理：%s", error)
            always_apply_context = ""
        if always_apply_context:
            actor_input = f"{always_apply_context}\n\n{actor_input}"
        return message, roster, actor_input, prepared

    async def _ensure_native_session(
        self, key: GroupConversationKey, message: IncomingMessage
    ) -> tuple[str, bool]:
        lock = self._create_locks.setdefault(key.as_str(), asyncio.Lock())
        async with lock:
            session_id = self._sessions.get(key)
            is_first_turn = session_id is None
            if session_id and not await self._session_has_required_vaults(
                session_id, message
            ):
                self._sessions.reset(key)
                await self._stop_consumer(key)
                session_id = None
                is_first_turn = True
            if not session_id:
                session_id = await self._create_session(key, message)
                log.info(
                    "%s 新话题已建 Session=%s",
                    message_log_tag(message),
                    session_id,
                )
            self._ensure_consumer(key, session_id, message)
            return session_id, is_first_turn

    def _ensure_consumer(
        self,
        key: GroupConversationKey,
        session_id: str,
        message: IncomingMessage,
    ) -> None:
        key_str = key.as_str()
        current = self._consumers.get(key_str)
        # #region debug-point A-B:consumer-state
        _debug_report(
            "A,B",
            "digital_employee.py:_ensure_consumer",
            "ensure consumer",
            {
                "key": key_str,
                "session_id": session_id,
                "has_consumer": current is not None,
                "consumer_done": current.done() if current is not None else None,
            },
        )
        # #endregion
        if current is not None and not current.done():
            return
        self._consumers[key_str] = self._loop.create_task(
            self._consume(session_id, key, message)
        )

    async def _send_native(
        self,
        key: GroupConversationKey,
        message: IncomingMessage,
        session_id: str,
        actor_input: str,
        prepared: list[PreparedAttachment],
    ) -> None:
        delay = BACKOFF_BASE_S
        for attempt in range(MAX_409_RETRIES + 1):
            try:
                await self._ark.send_message(session_id, actor_input)
                log.info(
                    "%s 已直发 Session=%s，input 长度=%d",
                    message_log_tag(message),
                    session_id,
                    len(actor_input),
                )
                return
            except ArkError as error:
                if error.status_code == 404:
                    self._sessions.reset(key)
                    await self._stop_consumer(key)
                    session_id, _ = await self._ensure_native_session(key, message)
                    await self._mount_attachments(session_id, prepared)
                    continue
                if not _is_runtime_busy(error) or attempt == MAX_409_RETRIES:
                    raise
                await asyncio.sleep(delay)
                delay = min(delay * 2, BACKOFF_CAP_S)

    async def _consume(
        self,
        session_id: str,
        key: GroupConversationKey,
        fallback_message: IncomingMessage,
    ) -> None:
        seen: set[str] = set()
        delivered_count = 0
        while self._sessions.get(key) == session_id:
            try:
                # #region debug-point B:stream-open
                _debug_report(
                    "B",
                    "digital_employee.py:_consume",
                    "opening event stream",
                    {
                        "session_id": session_id,
                        "delivered_count": delivered_count,
                    },
                )
                # #endregion
                async with self._ark._open_event_stream(session_id) as stream:  # noqa: SLF001
                    async for event in stream:
                        event_id = event.get("id")
                        if event_id and event_id in seen:
                            continue
                        if event_id:
                            seen.add(event_id)
                        event_type = event.get("type")
                        domains = self._native_tool_domains.setdefault(session_id, {})
                        remember_lark_cli_tool_domain(event, domains)
                        authorization = event_user_authorization_required(
                            event, domains
                        )
                        custom_call = event_custom_tool_call(event)
                        # #region debug-point A-D:event-received
                        _debug_report(
                            "A,B,C,D",
                            "digital_employee.py:_consume:event",
                            "event received",
                            {
                                "session_id": session_id,
                                "event_id": event_id,
                                "event_type": event_type,
                                "delivered_count": delivered_count,
                                "trigger_count": len(
                                    self._pending_reactions.get(key.as_str(), [])
                                ),
                            },
                        )
                        # #endregion
                        if authorization:
                            await self._handle_native_authorization(
                                key,
                                session_id,
                                fallback_message,
                                authorization,
                            )
                        elif custom_call:
                            output, is_error = await self._memory.handle_tool(
                                session_id,
                                custom_call["name"],
                                custom_call["arguments"],
                            )
                            await self._ark.send_custom_tool_result(
                                session_id,
                                custom_call["id"],
                                output,
                                is_error=is_error,
                            )
                        elif event_type == "agent.message":
                            text = event_text(event)
                            if (
                                text
                                and key.as_str() not in self._native_authorizing
                                and key.as_str() not in self._native_blocked_until_idle
                            ):
                                await self._deliver_native_message(
                                    key,
                                    fallback_message,
                                    text,
                                    session_id,
                                    delivered_count,
                                )
                                delivered_count += 1
                        elif event_type == "session.status_idle":
                            if event_requires_action(event):
                                continue
                            self._native_blocked_until_idle.discard(key.as_str())
                            if key.as_str() not in self._native_authorizing:
                                await self._clear_native_reactions(key)
                        elif event_type in (
                            "session.error",
                            "session.status_failed",
                        ):
                            if key.as_str() in self._native_blocked_until_idle:
                                self._native_blocked_until_idle.discard(key.as_str())
                                await self._clear_native_reactions(key)
                                continue
                            detail = event_error(event) or "未提供错误详情"
                            log.warning("[session=%s] 会话出错：%s", session_id, detail)
                            trigger = self._last_native_trigger(key) or fallback_message
                            await self._reply(
                                trigger,
                                "当前话题执行出错，请稍后重试或 @bot /new 重置。",
                            )
                            await self._clear_native_reactions(key)
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - 长连接中断后恢复
                # #region debug-point B-C:stream-error
                _debug_report(
                    "B,C",
                    "digital_employee.py:_consume:except",
                    "consumer stream failed",
                    {
                        "session_id": session_id,
                        "error_type": type(error).__name__,
                        "error": str(error)[:500],
                        "delivered_count": delivered_count,
                    },
                )
                # #endregion
                if self._sessions.get(key) != session_id:
                    break
                log.info("事件流中断，1s 后重连 session=%s", session_id)
                await asyncio.sleep(1.0)

    async def _deliver_native_message(
        self,
        key: GroupConversationKey,
        fallback_message: IncomingMessage,
        text: str,
        session_id: str,
        delivered_count: int,
    ) -> None:
        key_str = key.as_str()
        pending = self._pending_reactions.get(key_str, [])
        trigger, reaction_id = (
            pending[0] if pending else (fallback_message, None)
        )
        roster = await self._chat_roster(trigger)
        # #region debug-point C:before-reply
        _debug_report(
            "C",
            "digital_employee.py:_deliver_native_message",
            "replying agent message",
            {
                "session_id": session_id,
                "trigger_message_id": trigger.message_id,
                "delivered_count": delivered_count,
                "text_length": len(text),
            },
        )
        # #endregion
        thread_id = await self._reply(trigger, text, roster)
        self._bind_thread_session(key, trigger, session_id, thread_id)
        if pending and pending[0] == (trigger, reaction_id):
            pending.pop(0)
            self._native_inputs.pop((key_str, trigger.message_id), None)
            self._native_prepared.pop((key_str, trigger.message_id), None)
            if not pending:
                self._pending_reactions.pop(key_str, None)
            await self._unack(trigger, reaction_id)
        # #region debug-point C:after-reply
        _debug_report(
            "C",
            "digital_employee.py:_deliver_native_message",
            "reply completed",
            {
                "session_id": session_id,
                "trigger_message_id": trigger.message_id,
                "delivered_count": delivered_count + 1,
            },
        )
        # #endregion

    def _last_native_trigger(
        self, key: GroupConversationKey
    ) -> Optional[IncomingMessage]:
        pending = self._pending_reactions.get(key.as_str(), [])
        return pending[-1][0] if pending else None

    async def _clear_native_reactions(self, key: GroupConversationKey) -> None:
        pending = self._pending_reactions.pop(key.as_str(), [])
        for message, reaction_id in pending:
            self._native_inputs.pop((key.as_str(), message.message_id), None)
            self._native_prepared.pop((key.as_str(), message.message_id), None)
            await self._unack(message, reaction_id)

    async def _stop_consumer(self, key: GroupConversationKey) -> None:
        task = self._consumers.pop(key.as_str(), None)
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _create_session(
        self, key: GroupConversationKey, message: IncomingMessage
    ) -> str:
        vault_ids = (
            [self._config.lark_vault_id] if lark_cli_enabled(self._config) else []
        )
        if message.chat_type == "p2p":
            vault_ids.append(await self._user_auth.vault_id(message))
        vault_ids = list(dict.fromkeys(filter(None, vault_ids)))
        memory_scope, memory_store_id, memory_resources = (
            await self._memory.resources_for_message(message)
        )
        session_id = await self._ark.create_session(
            self._config.ark_agent_id,
            self._config.ark_environment_id,
            vault_ids=vault_ids or None,
            env_overrides=(
                build_lark_session_env(message, self._config.feishu_app_id)
                if lark_cli_enabled(self._config) or message.chat_type == "p2p"
                else None
            ),
            resources=memory_resources,
        )
        self._memory.bind_session(session_id, memory_scope, memory_store_id)
        self._sessions.save(key, session_id)
        save_session_vaults = getattr(self._sessions, "save_session_vaults", None)
        if save_session_vaults:
            save_session_vaults(session_id, vault_ids)
        if message.chat_type == "p2p":
            oauth = self._sessions.get_user_oauth(
                message.tenant_key, message.user_open_id
            )
            save_session_user_token = getattr(
                self._sessions, "save_session_user_token", None
            )
            if oauth and save_session_user_token:
                save_session_user_token(
                    session_id,
                    message.tenant_key,
                    message.user_open_id,
                    int(oauth["expires_at"]),
                )
        public_key = to_topic_key(message)
        if public_key != key:
            self._sessions.save(public_key, session_id)
        return session_id

    async def _session_has_required_vaults(
        self, session_id: str, message: IncomingMessage
    ) -> bool:
        if not self._memory.session_matches_message(session_id, message):
            return False
        if message.chat_type != "p2p":
            return True
        user_vault_id = await self._user_auth.vault_id(message)
        get_session_vaults = getattr(self._sessions, "get_session_vaults", None)
        vault_matches = bool(
            get_session_vaults
            and user_vault_id in get_session_vaults(session_id)
        )
        if not vault_matches:
            return False
        oauth = self._sessions.get_user_oauth(
            message.tenant_key, message.user_open_id
        )
        get_session_user_token = getattr(
            self._sessions, "get_session_user_token", None
        )
        bound = (
            get_session_user_token(session_id)
            if get_session_user_token
            else None
        )
        return bool(
            oauth
            and bound
            == (
                message.tenant_key,
                message.user_open_id,
                int(oauth["expires_at"]),
            )
        )

    async def _request_user_authorization(
        self,
        message: IncomingMessage,
        authorization: UserAuthorizationRequired,
        resume,
    ) -> None:
        retry_key = f"{message.tenant_key}:{message.message_id}"
        if message.chat_type != "p2p":
            raise RuntimeError(
                "群聊仅使用 Bot 身份，不能申请个人凭据；请私聊数字员工处理个人数据"
            )
        if retry_key in self._authorization_retries:
            raise RuntimeError("授权后仍未获得用户凭据，请重新授权或联系管理员")
        self._authorization_retries.add(retry_key)
        await self._user_auth.request(
            message,
            authorization.domain,
            authorization.missing_scopes,
            resume,
        )

    async def _resume_serial(
        self,
        message: IncomingMessage,
        key: GroupConversationKey,
        actor_input: str,
        prepared: list[PreparedAttachment],
        roster: dict,
    ) -> None:
        # Vault Credential 的值在 Session 创建时解析；原地更新 token 后旧 Session
        # 仍会继续使用占位/过期值，必须用同一 Vault 新建 Session。
        self._sessions.reset(key)
        replacement = await self._create_session(key, message)
        await self._mount_attachments(replacement, prepared)
        result = await self._ark.run(
            replacement,
            actor_input,
            self._config.session_timeout_ms,
            custom_tool_handler=lambda name, arguments: self._memory.handle_tool(
                replacement, name, arguments
            ),
        )
        if result.authorization_required:
            raise RuntimeError("授权后新 Session 仍未获得用户凭据，请联系管理员")
        thread_id = await self._reply(message, _result_to_text(result), roster)
        self._bind_thread_session(key, message, replacement, thread_id)

    async def _handle_native_authorization(
        self,
        key: GroupConversationKey,
        session_id: str,
        fallback_message: IncomingMessage,
        authorization: UserAuthorizationRequired,
    ) -> None:
        key_str = key.as_str()
        if key_str in self._native_authorizing:
            return
        pending = self._pending_reactions.get(key_str, [])
        trigger = pending[0][0] if pending else fallback_message
        if trigger.chat_type != "p2p":
            self._native_blocked_until_idle.add(key_str)
            await self._reply(
                trigger,
                "群聊仅使用 Bot 身份，不能读取成员个人数据；请私聊我后再发起该请求。",
            )
            await self._clear_native_reactions(key)
            return
        actor_input = self._native_inputs.get((key_str, trigger.message_id))
        if not actor_input:
            raise RuntimeError("缺少待续跑的原始请求")
        prepared = self._native_prepared.get((key_str, trigger.message_id), [])
        self._native_authorizing.add(key_str)

        async def _resume() -> None:
            current_session = self._sessions.get(key)
            if current_session != session_id:
                raise RuntimeError("授权期间 Session 已变化，请重新发送请求")
            # 方舟 Session 不热加载更新后的 Vault Credential，授权后重建 Session。
            await self._stop_consumer(key)
            self._sessions.reset(key)
            replacement = await self._create_session(key, trigger)
            await self._mount_attachments(replacement, prepared)
            self._ensure_consumer(key, replacement, trigger)
            self._native_authorizing.discard(key_str)
            await self._ark.send_message(replacement, actor_input)

        try:
            await self._request_user_authorization(trigger, authorization, _resume)
        except Exception as error:  # noqa: BLE001 - 授权入口失败需形成用户可见终态
            self._native_authorizing.discard(key_str)
            self._native_blocked_until_idle.add(key_str)
            await self._reply(trigger, f"无法发起用户授权：{str(error)[:180]}")
            await self._clear_native_reactions(key)

    async def _send_authorization_card(
        self, message: IncomingMessage, url: str, domain: str
    ) -> None:
        await self._loop.run_in_executor(
            None,
            self._sender.send_authorization_card,
            message.chat_id,
            url,
            domain,
        )

    async def _notify_authorization_failure(
        self, message: IncomingMessage, text: str
    ) -> None:
        key = self._thread_aliases.get(
            to_topic_key(message).as_str(), to_topic_key(message)
        )
        self._native_authorizing.discard(key.as_str())
        if self._execution_mode == "native-queue":
            await self._clear_native_reactions(key)
        await self._reply(message, text)

    def _bind_thread_session(
        self,
        source_key: GroupConversationKey,
        message: IncomingMessage,
        session_id: str,
        thread_id: Optional[str],
    ) -> None:
        if message.chat_type != "group" or not thread_id:
            return
        thread_key = GroupConversationKey(
            message.tenant_key, message.chat_id, thread_id
        )
        self._sessions.save(thread_key, session_id)
        if self._execution_mode == "native-queue" and thread_key != source_key:
            self._thread_aliases[thread_key.as_str()] = source_key
        log.info(
            "%s 已绑定 thread_id=%s 到 Session=%s",
            message_log_tag(message),
            thread_id,
            session_id,
        )

    async def _refresh_lark_cli_token(self) -> None:
        """在 token 临近过期前由 Bot 主机刷新 Vault，避免长寿命 Session 失去鉴权。"""
        if not lark_cli_enabled(self._config):
            return
        now = self._loop.time()
        if now < self._lark_token_refresh_at:
            return
        async with self._lark_token_lock:
            now = self._loop.time()
            if now < self._lark_token_refresh_at:
                return
            token = await fetch_feishu_tenant_access_token(
                self._config.feishu_app_id,
                self._config.feishu_app_secret,
            )
            await update_lark_cli_vault_token(
                self._ark,
                self._config.lark_vault_id,
                token.value,
            )
            self._lark_token_refresh_at = now + max(30, token.expires_in - 300)
            log.info(
                "已刷新 lark-cli Bot tenant token，有效期=%ss",
                token.expires_in,
            )

    async def _reply(
        self, message: IncomingMessage, text: str, roster: Optional[dict] = None
    ) -> Optional[str]:
        if message.chat_type == "group" and message.message_id:
            return await self._loop.run_in_executor(
                None, self._sender.reply_in_thread, message.message_id, text, roster
            )
        await self._loop.run_in_executor(
            None, self._sender.send_to_chat, message.chat_id, text, roster
        )
        return None

    async def _ack(self, message: IncomingMessage) -> Optional[str]:
        if message.message_id:
            try:
                return await self._loop.run_in_executor(
                    None, self._sender.react, message.message_id, "OneSecond"
                )
            except Exception as error:  # noqa: BLE001 - 回执失败不影响正式回复
                log.warning("发送「稍等」表情失败：%s", error)
                return None
        return None

    async def _unack(self, message: IncomingMessage, reaction_id: Optional[str]) -> None:
        if not reaction_id or not message.message_id:
            return
        try:
            await self._loop.run_in_executor(
                None, self._sender.delete_reaction, message.message_id, reaction_id
            )
        except Exception as error:  # noqa: BLE001 - 正式回复已经完成
            log.warning("撤回「稍等」表情失败：%s", error)

    async def _chat_roster(self, message: IncomingMessage) -> dict:
        if not message.chat_id:
            return {}
        try:
            return await self._loop.run_in_executor(
                None, self._sender.chat_roster, message.chat_id
            )
        except Exception as error:  # noqa: BLE001 - 名册失败只降级为 open_id / 不可点击 @
            log.warning("获取会话成员名册失败：%s", error)
            return {}

    async def _topic_history(self, message: IncomingMessage) -> list[HistoryMessage]:
        """读取当前话题历史；主时间线的首次 @bot 尚无话题，必须返回空。"""
        if message.chat_type != "group" or not has_topic_coordinates(message):
            return []
        try:
            return await self._loop.run_in_executor(
                None, self._sender.list_messages, message
            )
        except Exception as error:  # noqa: BLE001 - 历史读取失败降级为仅当前请求
            log.warning("读取当前话题历史失败，本轮只发送当前消息：%s", error)
            return []

    async def _topic_root_context(
        self, message: IncomingMessage
    ) -> list[HistoryMessage]:
        """精确补入话题根；根消息可能就是用户引用的文件。"""
        if not message.root_id or message.root_id == message.message_id:
            return []
        try:
            raw = await self._loop.run_in_executor(
                None, self._sender.get_message, message.root_id
            )
            if not raw:
                return []
            bot_open_id = await self._loop.run_in_executor(
                None, self._sender.bot_open_id
            )
            normalized = normalize_history_item(raw, bot_open_id)
            return [normalized] if normalized is not None else []
        except Exception as error:  # noqa: BLE001 - 根消息读取失败不影响当前请求
            log.warning("读取话题根消息失败，本轮只发送当前消息：%s", error)
            return []

    async def _explicit_quote(
        self, message: IncomingMessage
    ) -> tuple[list[QuotedMessage], Optional[HistoryMessage]]:
        """只读取用户显式引用，不读取周围群消息。"""
        if not message.reply_to_message_id:
            return [], None
        try:
            quote_chain = await self._loop.run_in_executor(
                None, self._sender.load_quote_chain, message
            )
            raw = await self._loop.run_in_executor(
                None, self._sender.get_message, message.reply_to_message_id
            )
            if not raw:
                return quote_chain, None
            bot_open_id = await self._loop.run_in_executor(None, self._sender.bot_open_id)
            return quote_chain, normalize_history_item(raw, bot_open_id)
        except Exception as error:  # noqa: BLE001 - 引用读取失败不影响当前消息
            log.warning("读取显式引用失败，本轮只发送当前消息：%s", error)
            return [], None

    async def _prepare_attachments(
        self, message: IncomingMessage, resources: list[ResourceRef]
    ) -> tuple[list[PreparedAttachment], list[str]]:
        if not resources:
            return [], []
        if not multimodal_enabled():
            return [], ["[附件已忽略：多模态未开启]"]

        async def _download(ref: ResourceRef) -> bytes:
            return await self._loop.run_in_executor(
                None,
                self._sender.download_resource,
                ref.message_id or message.message_id,
                ref.file_key,
                ref.type,
            )

        async def _upload(name: str, mime: str, data: bytes) -> str:
            return await self._ark.upload_file(name, mime, data)

        return await prepare_attachments(
            message,
            _download,
            _upload,
            lookup_file_id=getattr(self._sessions, "get_attachment", None),
            save_file_id=getattr(self._sessions, "save_attachment", None),
            resources=resources,
        )

    async def _mount_attachments(
        self, session_id: str, prepared: list[PreparedAttachment]
    ) -> None:
        is_mounted = getattr(self._sessions, "is_attachment_mounted", None)
        mark_mounted = getattr(self._sessions, "mark_attachment_mounted", None)
        for item in prepared:
            if not item.file_id:
                continue
            if is_mounted and item.file_key and is_mounted(session_id, item.file_key):
                continue
            try:
                await self._ark.add_session_file(session_id, item.file_id, item.mount_path)
                if mark_mounted and item.file_key:
                    mark_mounted(session_id, item.file_key)
            except Exception as error:  # noqa: BLE001 - 单附件失败不应终止整个话题
                log.warning("挂载附件「%s」失败：%s", item.name, error)


def _dedupe_resources(resources: list[ResourceRef]) -> list[ResourceRef]:
    result: list[ResourceRef] = []
    seen: set[str] = set()
    for ref in resources:
        if ref.file_key and ref.file_key not in seen:
            seen.add(ref.file_key)
            result.append(ref)
    return result


def _is_authorization_retry_request(text: str) -> bool:
    normalized = "".join((text or "").lower().split())
    return any(
        marker in normalized
        for marker in (
            "重新授权",
            "重新生成",
            "链接失效",
            "链接过期",
            "卡片失效",
            "卡片过期",
        )
    )


def _with_roster_name(
    message: IncomingMessage, roster: dict[str, str]
) -> IncomingMessage:
    """SDK 未解析发言人姓名时，用群名册按 open_id 反查，避免把长 ID 发给 Agent。"""
    if message.user_name or not message.user_open_id:
        return message
    name = next(
        (candidate for candidate, open_id in roster.items() if open_id == message.user_open_id),
        "",
    )
    return replace(message, user_name=name) if name else message


def _with_roster_history_names(
    history: list[HistoryMessage], roster: dict[str, str]
) -> list[HistoryMessage]:
    """补齐历史消息发送者姓名，避免文件等非文本消息回退显示 open_id。"""
    names_by_open_id = {open_id: name for name, open_id in roster.items()}
    return [
        replace(
            item,
            sender_name=names_by_open_id.get(item.sender_open_id, ""),
        )
        if not item.sender_name and item.sender_open_id in names_by_open_id
        else item
        for item in history
    ]


def _result_to_text(result: RunResult) -> str:
    if result.terminal == "failed":
        detail = f"：{result.error}" if result.error else ""
        raise RuntimeError(f"Agent Session 执行失败{detail}")
    if not result.messages:
        raise RuntimeError("Agent Session 已结束，但没有产生回复")
    return result.messages[-1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="按飞书话题隔离的数字员工")
    parser.add_argument(
        "--execution-mode",
        choices=("serial", "native-queue"),
        default="serial",
        help="serial=客户端串行；native-queue=方舟运行中队列（默认：serial）",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    setup_logging(level="INFO")
    config = load_group_bot_config()
    ark = ArkClient(config.ark_api_key, config.ark_base_url)
    sender = FeishuSender(config.feishu_app_id, config.feishu_app_secret)

    loop = asyncio.new_event_loop()
    bot = TopicSessionBot(
        config, ark, sender, loop, execution_mode=args.execution_mode
    )
    threading.Thread(target=loop.run_forever, name="topic-bot-loop", daemon=True).start()

    print("数字员工阿J已启动：")
    print(f"- 飞书 App ID：{config.feishu_app_id}")
    print(f"- Agent ID：{config.ark_agent_id}")
    print(f"- 执行模式：{args.execution_mode}")
    print("- 策略：主时间线 @bot 创建话题；之后仅 @bot 时回复，并带上当前话题增量。")
    print("- 指令：在当前话题发送 @bot /new，只重置该话题的 Session。")
    start_feishu_gateway(config.feishu_app_id, config.feishu_app_secret, bot)


if __name__ == "__main__":
    main()
