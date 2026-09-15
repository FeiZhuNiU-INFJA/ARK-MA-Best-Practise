"""方舟原生队列（运行中直发 + 吸收/合并）——更接近 Claude Tag 的异步协作。

思路（方舟原生队列方案，依据 common/docs/火山方舟_ManagedAgents_docs.md「运行中继续发送消息」L3183+）：
  - 群里所有人 @ bot 共享同一个方舟 Session。
  - **不在客户端排队**：消息一到就 send_message 直接打进 Session，哪怕它还在 running。
    方舟把它写入「运行中待处理队列」，等 Agent 执行到「可调度边界」（模型请求结束 /
    工具结果返回 / 回合结束）再送入后续模型请求。
  - 由此带来「吸收/合并」：同一边界前堆积的多条消息可能被合并进一次模型请求，
    Agent 不保证为每条消息各回一条（L3193）。适合“同一件事多人接力补充”，
    代价是并发问不同事时回复可能揉在一起。
  - 待处理队列满会返回 409 RuntimeBusy（L3195）：不能猛重试，需退避。

与客户端串行方案的关键区别：
  - 客户端串行：客户端 KeyedQueue 串行，上一轮 idle 才发下一条，每人各得干净回复、无 409。
  - 方舟原生队列：直发，靠方舟侧队列吸收合并；用一个常驻事件流消费协程把回复回到群里。

回复策略：因为可能合并、一条回复可能同时面向多人，这里回到**群会话**（不 reply
到某条消息），并靠 system prompt 要求 Agent 分别 @ 到对应的人。

运行：
  set -a && source ~/.arkagent/config.env && set +a
  export GROUP_BOT_AGENT_ID=<用 create_group_agent.py 建出的 agent id>
  python scenarios/feishu-bot/cases/group-bot/ma_native_queue_bot.py
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import threading

from shared import (
    GroupBotConfig,
    GroupConversationKey,
    SqliteSessionMap,
    build_windowed_input,
    is_authorized,
    load_group_bot_config,
    message_log_tag,
    setup_logging,
    should_handle,
    to_group_key,
)

from arkagent.ark import ArkClient, ArkError, event_text
from arkagent.feishu import FeishuSender, IncomingMessage, start_feishu_gateway

log = logging.getLogger("group_bot.ma_native")

MAX_409_RETRIES = 5
BACKOFF_BASE_S = 2.0
BACKOFF_CAP_S = 15.0


def _is_runtime_busy(error: Exception) -> bool:
    """方舟队列满会返回 409 RuntimeBusy。优先用 ArkError 的结构化 status_code 判定，
    非 ArkError（或没带状态码）时退回按字符串识别。"""
    if isinstance(error, ArkError) and error.status_code == 409:
        return True
    text = str(error)
    return " 409" in text or "RuntimeBusy" in text


class ConcurrentGroupBot:
    """方舟原生队列版：消息直发，不在客户端排队；每个 Session 一个常驻消费协程。"""

    def __init__(
        self,
        config: GroupBotConfig,
        ark: ArkClient,
        sender: FeishuSender,
        loop: asyncio.AbstractEventLoop,
        sessions: object | None = None,
    ) -> None:
        self._config = config
        self._ark = ark
        self._sender = sender
        self._loop = loop
        # 默认落 SQLite（持久化，重启不丢）；测试可注入临时/内存实现，避免碰 ~/.arkagent。
        # 只要满足 get/save/reset/claim_event 四个方法即可（鸭子类型，同 InMemorySessionMap）。
        self._sessions = sessions if sessions is not None else SqliteSessionMap()
        # 每个群 key 一个「建会话锁」，避免并发首条消息重复建 Session。
        self._create_locks: dict[str, asyncio.Lock] = {}
        self._consumers: dict[str, asyncio.Task] = {}
        # 每个群 key 累积的「稍等」表情，键=key.as_str()，值=[(message_id, reaction_id)]。
        # _ack 贴表情后记进来；一个回合结束（回复已发）或出错后整批撤回——方舟原生队列
        # 一次回合可能合并多条触发消息，做不到逐条精确对应，故按 key 批量收尾。
        self._pending_reactions: dict[str, list[tuple[str, str]]] = {}

    def accept(self, message: IncomingMessage) -> bool:
        tag = message_log_tag(message)
        log.info("%s 收到消息，text=%r", tag, message.text[:80])
        if not should_handle(message):
            log.info("%s 丢弃：群消息未 @bot 或空文本（should_handle=False）", tag)
            return False
        if not self._sessions.claim_event(message.event_id):
            log.info("%s 丢弃：event 已处理过（去重命中）", tag)
            return False
        # 直接投递处理协程（不串行化）——多条消息可并发进入 _handle。
        log.info("%s 直投处理协程（方舟原生队列，不客户端排队）", tag)
        self._loop.call_soon_threadsafe(
            lambda: self._loop.create_task(self._handle(message))
        )
        return True

    async def _send_to_chat(self, chat_id: str, text: str) -> None:
        await self._loop.run_in_executor(None, self._sender.send_to_chat, chat_id, text)

    async def _ack(self, message: IncomingMessage, key: GroupConversationKey) -> None:
        """已收到回执：在触发消息下贴一个「稍等」(OneSecond) 表情，替代之前那句
        「正在创建共享会话」的文字回执——更轻量、不刷屏（类似 Claude Tag 的 OnIt）。
        表情回应失败不该拖垮本轮，吞掉即可。

        贴上后把 reaction_id 记进本 key 的待撤回表里；等这一回合结束（合并回复已发出）
        或出错时，由 _consume 调 _clear_reactions 整批撤回。方舟原生队列一次回合可能合并
        多条触发消息，做不到逐条精确对应，故按 key 批量收尾。
        """
        if message.chat_type != "group" or not message.message_id:
            return
        try:
            reaction_id = await self._loop.run_in_executor(
                None, self._sender.react, message.message_id, "OneSecond"
            )
        except Exception as error:  # noqa: BLE001 - 回执失败不影响正式回复
            log.warning("发送「稍等」表情失败：%s", error)
            return
        if reaction_id:
            self._pending_reactions.setdefault(key.as_str(), []).append(
                (message.message_id, reaction_id)
            )

    async def _clear_reactions(self, key: GroupConversationKey) -> None:
        """把本 key 累积的「稍等」表情整批撤回——回合结束/出错后回执使命完成。
        逐个撤回，单个失败不影响其余，吞掉即可。"""
        pending = self._pending_reactions.pop(key.as_str(), [])
        for message_id, reaction_id in pending:
            try:
                await self._loop.run_in_executor(
                    None, self._sender.delete_reaction, message_id, reaction_id
                )
            except Exception as error:  # noqa: BLE001 - 撤回失败不影响已发出的回复
                log.warning("撤回「稍等」表情失败：%s", error)

    async def _handle(self, message: IncomingMessage) -> None:
        tag = message_log_tag(message)
        log.info("%s 开始处理", tag)
        try:
            if not is_authorized(self._config, message.user_open_id):
                log.info("%s 未授权，拒绝：open_id=%s", tag, message.user_open_id)
                await self._send_to_chat(message.chat_id, "当前用户未授权。请联系管理员把你的 open_id 加入白名单。")
                return

            key = to_group_key(message)
            if message.text.strip() == "/new":
                log.info("%s 指令 /new：重置本群会话并停消费协程", tag)
                self._sessions.reset(key)
                await self._stop_consumer(key)
                # 停了消费协程后没人再撤回，这里把本 key 遗留的「稍等」表情清掉。
                await self._clear_reactions(key)
                await self._send_to_chat(message.chat_id, "已重置本群会话，下一条消息会创建新的共享 Session。")
                return

            # 已收到回执：直接在触发消息下贴「稍等」表情（不管有没有现成会话都一样）。
            # 记进本 key 的待撤回表，回合结束/出错时由 _consume 批量撤回。
            await self._ack(message, key)
            session_id = await self._ensure_session(message, key)
            await self._post_message(session_id, message)
        except Exception as error:  # noqa: BLE001
            log.exception("处理消息失败")
            await self._send_to_chat(message.chat_id, f"执行失败：{str(error)[:240]}")
            # 本条没能发进方舟（如 409 退避耗尽），回合的 idle 不会为它触发，
            # 这里兜底把本 key 遗留的「稍等」表情撤回。
            await self._clear_reactions(to_group_key(message))

    async def _ensure_session(self, message: IncomingMessage, key: GroupConversationKey) -> str:
        tag = message_log_tag(message)
        lock = self._create_locks.setdefault(key.as_str(), asyncio.Lock())
        async with lock:
            session_id = self._sessions.get(key)
            if session_id:
                log.info("%s 命中已有 Session=%s", tag, session_id)
                return session_id
            log.info("%s 无现成 Session，创建新的共享 Session", tag)
            session_id = await self._ark.create_session(
                self._config.ark_agent_id,
                self._config.ark_environment_id,
                # Bot-only：不注入个人 open_id、不挂个人 Vault/Memory。
            )
            self._sessions.save(key, session_id)
            # 起一个常驻消费协程读事件流，把 Agent 回复回到群里。
            self._consumers[key.as_str()] = self._loop.create_task(
                self._consume(session_id, message.chat_id, key)
            )
            log.info("%s 已建 Session=%s，并起消费协程", tag, session_id)
            return session_id

    async def _post_message(self, session_id: str, message: IncomingMessage) -> None:
        """直发 user.message；running 时方舟写入待处理队列。满队列 409 则退避重试。

        另兜一层 session 失效：持久化的 session_id 可能已在方舟侧过期/被清（重启后尤甚），
        send_message 会 404。此时重置映射、停掉旧消费协程、重建一个新 Session（并起新消费
        协程），换用新 session_id 重发一次。
        """
        tag = message_log_tag(message)
        key = to_group_key(message)
        actor_input = await self._windowed_input(message)
        log.debug("%s 完整 input：\n%s", tag, actor_input)
        delay = BACKOFF_BASE_S
        for attempt in range(MAX_409_RETRIES + 1):
            try:
                await self._ark.send_message(session_id, actor_input)
                log.info("%s 已直发方舟 Session=%s，input 长度=%d", tag, session_id, len(actor_input))
                return
            except ArkError as error:
                if error.status_code == 404:
                    # Session 失效：清掉旧映射与旧消费协程，重建后换新 session_id 重发。
                    log.warning("%s Session 已失效(404)，重建后重发：%s", tag, error)
                    self._sessions.reset(key)
                    await self._stop_consumer(key)
                    session_id = await self._ensure_session(message, key)
                    log.info("%s 已重建 Session=%s，重发本条", tag, session_id)
                    continue
                if not _is_runtime_busy(error) or attempt == MAX_409_RETRIES:
                    raise
                # 队列满：不要猛重试。退避等待 Agent 消费掉队列后再发。
                log.info("%s 队列忙(409)，第 %d 次退避 %.1fs 后重试", tag, attempt + 1, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, BACKOFF_CAP_S)

    async def _windowed_input(self, message: IncomingMessage) -> str:
        """读群历史 → 取「倒数第二次 @bot → 当前」窗口 → 拼成一条 user message。

        与客户端串行方案完全一致：群聊才读历史，私聊直接空窗口；lark-oapi 同步调用丢 executor，
        读失败降级为无上下文。方舟原生队列直发到 running 中的 Session，这条 user message 会被
        方舟写入待处理队列、在可调度边界处消费——窗口本身仍是「这一条消息」的完整上下文。
        """
        if message.chat_type != "group":
            return build_windowed_input(message, [])
        try:
            history = await self._loop.run_in_executor(None, self._sender.list_messages, message)
        except Exception as error:  # noqa: BLE001 - 历史读失败不该拖垮本轮，降级为无上下文
            log.warning("读取群历史失败，本轮不带上下文：%s", error)
            history = []
        return build_windowed_input(message, history)

    async def _consume(self, session_id: str, chat_id: str, key: GroupConversationKey) -> None:
        """常驻读取 Session 事件流：每到一个回合结束(idle)，把该回合最后一条
        agent.message 作为合并回复发到群。断线自动重连，直到会话被 /new 重置。"""
        seen: set[str] = set()
        pending: list[str] = []
        while self._sessions.get(key) == session_id:
            try:
                async with self._ark._open_event_stream(session_id) as stream:  # noqa: SLF001 - 复用客户端流
                    async for event in stream:
                        eid = event.get("id")
                        if eid and eid in seen:
                            continue
                        if eid:
                            seen.add(eid)
                        etype = event.get("type")
                        if etype == "agent.message":
                            body = event_text(event)
                            if body:
                                pending.append(body)
                        elif etype == "session.status_idle":
                            # 一个可调度回合结束：把合并后的最终回复发出去，再撤回本回合
                            # 累积的「稍等」表情（这些触发消息已被这条合并回复回应）。
                            # 仅在确有回复发出时撤回——空 idle（如刚建会话、消息还没被消费）
                            # 不动表情，避免把刚贴上的「稍等」提前撤掉。
                            if pending:
                                await self._send_to_chat(chat_id, pending[-1])
                                log.info("[session=%s] 回合结束，回复已发出", session_id)
                                pending.clear()
                                await self._clear_reactions(key)
                        elif etype in ("session.error", "session.status_failed"):
                            log.warning("[session=%s] 会话出错，回执错误提示", session_id)
                            await self._send_to_chat(chat_id, "本群会话执行出错，请稍后重试或 /new 重置。")
                            pending.clear()
                            await self._clear_reactions(key)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - 断线重连
                if self._sessions.get(key) != session_id:
                    break
                log.info("事件流中断，1s 后重连 session=%s", session_id)
                await asyncio.sleep(1.0)

    async def _stop_consumer(self, key: GroupConversationKey) -> None:
        task = self._consumers.pop(key.as_str(), None)
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


def main() -> None:
    setup_logging(level="INFO")
    config = load_group_bot_config()

    ark = ArkClient(config.ark_api_key, config.ark_base_url)
    sender = FeishuSender(config.feishu_app_id, config.feishu_app_secret)

    loop = asyncio.new_event_loop()
    bot = ConcurrentGroupBot(config, ark, sender, loop)
    threading.Thread(target=loop.run_forever, name="group-bot-loop", daemon=True).start()

    print("方舟原生队列 Bot 已启动：")
    print(f"- 飞书 App ID：{config.feishu_app_id}")
    print(f"- 群聊共享 Agent ID：{config.ark_agent_id}")
    print("- 策略：消息直发方舟，running 中由服务端队列吸收/合并；不客户端排队。")
    print("在群里让多人几乎同时 @ 这个 bot，观察消息被吸收/合并的效果。指令：/new 重置本群会话。")
    start_feishu_gateway(config.feishu_app_id, config.feishu_app_secret, bot)


if __name__ == "__main__":
    main()
