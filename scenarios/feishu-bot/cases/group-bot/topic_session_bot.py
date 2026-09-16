"""话题 Session Bot：一次 @bot 发起一个飞书话题，一个话题对应一个方舟 Session。

交互规则：
  - 主时间线里 @bot：以该消息为根创建新话题，并创建独立 Session。
  - 话题内普通消息：不触发回复；下一次 @bot 时作为该轮上下文一并送入 Session。
  - 其他主时间线消息、未登记话题里的普通消息：忽略。
  - 每轮只读取当前话题中「上一次 @bot 之后到当前」的窗口，不读取主群或其他话题。

运行：
  set -a && source ~/.arkagent/config.env && set +a
  python scenarios/feishu-bot/cases/group-bot/topic_session_bot.py
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
from dataclasses import replace
from pathlib import Path
from typing import Optional

from shared import (
    GroupBotConfig,
    GroupConversationKey,
    PreparedAttachment,
    SqliteSessionMap,
    build_lark_session_env,
    build_windowed_input,
    collect_round_resources,
    is_authorized,
    is_reset_command,
    lark_cli_enabled,
    load_group_bot_config,
    message_log_tag,
    multimodal_enabled,
    prepare_attachments,
    setup_logging,
)

from arkagent.ark import ArkClient, ArkError, RunResult
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


def to_topic_key(message: IncomingMessage) -> GroupConversationKey:
    """返回稳定话题键；首条主时间线消息自身就是新话题的根。"""
    topic_id = ""
    if message.chat_type == "group":
        topic_id = message.root_id or message.thread_id or message.message_id
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
    """严格按飞书话题隔离的串行 Bot。"""

    def __init__(
        self,
        config: GroupBotConfig,
        ark: ArkClient,
        sender: FeishuSender,
        loop: asyncio.AbstractEventLoop,
        sessions: Optional[object] = None,
    ) -> None:
        self._config = config
        self._ark = ark
        self._sender = sender
        self._loop = loop
        db_path = os.environ.get("TOPIC_BOT_DB_PATH", DEFAULT_TOPIC_DB_PATH)
        self._sessions = sessions if sessions is not None else SqliteSessionMap(db_path)
        self._queue = KeyedQueue()

    def accept(self, message: IncomingMessage) -> bool:
        """WS 同步入口：群聊只有 @bot 才触发运行和回复。"""
        tag = message_log_tag(message)
        if not message.text.strip() and not message.resources:
            log.info("%s 丢弃：空消息且无附件", tag)
            return False

        key = to_topic_key(message)
        if message.chat_type == "group" and not message.mentioned_bot:
            log.info("%s 丢弃：群消息未 @bot（保留在话题历史，等下次 @bot 时读取）", tag)
            return False

        if not self._sessions.claim_event(message.event_id):
            log.info("%s 丢弃：event 已处理过", tag)
            return False

        def _enqueue() -> None:
            log.info("%s 入队，topic_key=%s", tag, key.as_str())
            self._queue.enqueue(key.as_str(), lambda: self._run(message, key))

        self._loop.call_soon_threadsafe(_enqueue)
        return True

    async def _run(self, message: IncomingMessage, key: GroupConversationKey) -> None:
        try:
            await self._process(message, key)
        except Exception:  # noqa: BLE001 - 单轮失败不能拖垮同话题队列
            log.exception("处理话题消息失败")
            await self._reply(message, "执行失败，请稍后重试；若持续失败，请在当前话题发送 /new。")

    async def _process(self, message: IncomingMessage, key: GroupConversationKey) -> None:
        tag = message_log_tag(message)
        if not is_authorized(self._config, message.user_open_id):
            await self._reply(message, "当前用户未授权。请联系管理员把你的 open_id 加入白名单。")
            return

        if is_reset_command(message.text):
            self._sessions.reset(key)
            session_id = await self._create_session(key, message)
            log.info("%s 当前话题已切换到新 Session=%s", tag, session_id)
            await self._reply(message, "已重置当前话题的 Session；后续消息仍留在本话题。")
            return

        reaction_id = await self._ack(message)
        try:
            session_id = self._sessions.get(key)
            if not session_id:
                session_id = await self._create_session(key, message)
                log.info("%s 新话题已建 Session=%s", tag, session_id)

            roster = await self._chat_roster(message)
            message = _with_roster_name(message, roster)
            history = await self._topic_history(message)
            history = _with_roster_history_names(history, roster)
            topic_delta = select_topic_delta(history)
            quote_chain, quoted_resource = await self._explicit_quote(message)
            resources = collect_round_resources(
                message, history, [], selected_history=topic_delta
            )
            if quoted_resource is not None:
                resources.extend(quoted_resource.resources)
            resources = _dedupe_resources(resources)

            prepared, notices = await self._prepare_attachments(message, resources)
            await self._mount_attachments(session_id, prepared)

            # 只读当前 thread，并排除已进入 Session 的上一次 @bot；绝不读取其他话题。
            actor_input = build_windowed_input(
                message,
                history=history,
                quote_chain=quote_chain,
                thread_context=[],
                prepared=prepared,
                notices=notices,
                selected_history=topic_delta,
            )
            log.info("%s 发往 Session=%s，input 长度=%d", tag, session_id, len(actor_input))
            log.debug("%s 完整 input：\n%s", tag, actor_input)
            try:
                result = await self._ark.run(
                    session_id, actor_input, self._config.session_timeout_ms
                )
            except ArkError as error:
                if error.status_code != 404:
                    raise
                session_id = await self._create_session(key, message)
                await self._mount_attachments(session_id, prepared)
                result = await self._ark.run(
                    session_id, actor_input, self._config.session_timeout_ms
                )

            await self._reply(message, _result_to_text(result), roster)
        finally:
            await self._unack(message, reaction_id)

    async def _create_session(
        self, key: GroupConversationKey, message: IncomingMessage
    ) -> str:
        session_id = await self._ark.create_session(
            self._config.ark_agent_id,
            self._config.ark_environment_id,
            vault_ids=[self._config.lark_vault_id] if lark_cli_enabled(self._config) else None,
            env_overrides=build_lark_session_env(message) if lark_cli_enabled(self._config) else None,
        )
        self._sessions.save(key, session_id)
        return session_id

    async def _reply(
        self, message: IncomingMessage, text: str, roster: Optional[dict] = None
    ) -> None:
        if message.chat_type == "group" and message.message_id:
            await self._loop.run_in_executor(
                None, self._sender.reply_in_thread, message.message_id, text, roster
            )
        else:
            await self._loop.run_in_executor(
                None, self._sender.send_to_chat, message.chat_id, text, roster
            )

    async def _ack(self, message: IncomingMessage) -> Optional[str]:
        if message.chat_type == "group" and message.message_id:
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
        if message.chat_type != "group" or not message.chat_id:
            return {}
        try:
            return await self._loop.run_in_executor(
                None, self._sender.chat_roster, message.chat_id
            )
        except Exception as error:  # noqa: BLE001 - 名册失败只降级为不可点击 @
            log.warning("获取群成员名册失败：%s", error)
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


def main() -> None:
    setup_logging(level="INFO")
    config = load_group_bot_config()
    ark = ArkClient(config.ark_api_key, config.ark_base_url)
    sender = FeishuSender(config.feishu_app_id, config.feishu_app_secret)

    loop = asyncio.new_event_loop()
    bot = TopicSessionBot(config, ark, sender, loop)
    threading.Thread(target=loop.run_forever, name="topic-bot-loop", daemon=True).start()

    print("话题 Session Bot 已启动：")
    print(f"- 飞书 App ID：{config.feishu_app_id}")
    print(f"- Agent ID：{config.ark_agent_id}")
    print("- 策略：主时间线 @bot 创建话题；之后仅 @bot 时回复，并带上当前话题增量。")
    print("- 指令：在当前话题发送 @bot /new，只重置该话题的 Session。")
    start_feishu_gateway(config.feishu_app_id, config.feishu_app_secret, bot)


if __name__ == "__main__":
    main()
