"""客户端串行（KeyedQueue）——每人各得一条干净回复。

思路（客户端串行方案）：
  - 群里所有人 @ bot 共享同一个方舟 Session（to_group_key 抹掉了 user_open_id）。
  - 用 KeyedQueue 按群 key 串行：**上一轮跑到 idle（end_turn）才发下一条**，
    因此每次 POST 时 Session 都是空闲的，从根上不进方舟的“运行中待处理队列”，
    不会发生消息合并，也不会触发 409 RuntimeBusy —— 每条消息各自独立成轮、
    各得一条清清楚楚回给发问人的答复。
  - 代价：后到的人要排队。排队期间给一句“正在处理，请稍候”的可见回执
    （类似 Claude Tag 的 OnIt 表情）。

运行：
  set -a && source ~/.arkagent/config.env && set +a   # 或自行 export 相关变量
  export GROUP_BOT_AGENT_ID=<用 create_group_agent.py 建出的 agent id>
  python scenarios/feishu-bot/cases/group-bot/client_serial_bot.py
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Optional

from shared import (  # 同目录脚本，直接导入
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

# shared 已把仓库根加入 sys.path，这里能 import 到主包基础设施。
from arkagent.ark import ArkClient, ArkError, RunResult
from arkagent.feishu import FeishuSender, IncomingMessage, start_feishu_gateway
from arkagent.gateway import KeyedQueue

log = logging.getLogger("group_bot.client_serial")


class SerialGroupBot:
    """客户端串行版群聊 bot。accept() 由 WS 线程调用，处理协程投递到事件循环。"""

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
        # 默认落 SQLite（持久化，重启不丢）；测试可注入临时/内存实现，避免碰 ~/.arkagent。
        # 只要满足 get/save/reset/claim_event 四个方法即可（鸭子类型，同 InMemorySessionMap）。
        self._sessions = sessions if sessions is not None else SqliteSessionMap()
        self._queue = KeyedQueue()

    # WS 线程入口：只做去重 + 投递，满足飞书 3 秒约束。
    def accept(self, message: IncomingMessage) -> bool:
        tag = message_log_tag(message)
        log.info("%s 收到消息，text=%r", tag, message.text[:80])
        if not should_handle(message):
            log.info("%s 丢弃：群消息未 @bot 或空文本（should_handle=False）", tag)
            return False
        if not self._sessions.claim_event(message.event_id):
            log.info("%s 丢弃：event 已处理过（去重命中）", tag)
            return False
        key = to_group_key(message)

        def _enqueue() -> None:
            # KeyedQueue 保证同一群 key 的任务按到达顺序串行执行。
            log.info("%s 入队串行处理，key=%s", tag, key.as_str())
            self._queue.enqueue(key.as_str(), lambda: self._run(message, key))

        self._loop.call_soon_threadsafe(_enqueue)
        return True

    async def _reply(self, message: IncomingMessage, text: str) -> None:
        # 群里回复到原消息（reply），私聊直接发到会话。lark-oapi 是同步调用，丢到 executor。
        if message.chat_type == "group" and message.message_id:
            await self._loop.run_in_executor(None, self._sender.reply, message.message_id, text)
        else:
            await self._loop.run_in_executor(None, self._sender.send_to_chat, message.chat_id, text)

    async def _ack(self, message: IncomingMessage) -> Optional[str]:
        """已收到回执：群里直接在原消息下贴一个「稍等」(OneSecond) 表情，
        比发一条文字消息更轻量、不刷屏（类似 Claude Tag 的 OnIt）。
        私聊没有可回应的原消息则退回一句文字。表情回应失败不该拖垮本轮，吞掉即可。
        返回本次表情的 reaction_id（供回复完成后撤回）；私聊或失败时返回 None。"""
        if message.chat_type == "group" and message.message_id:
            try:
                return await self._loop.run_in_executor(
                    None, self._sender.react, message.message_id, "OneSecond"
                )
            except Exception as error:  # noqa: BLE001 - 回执失败不影响正式回复
                log.warning("发送「稍等」表情失败：%s", error)
                return None
        await self._reply(message, "已收到，正在处理，请稍候。")
        return None

    async def _unack(self, message: IncomingMessage, reaction_id: Optional[str]) -> None:
        """回复已正式发出：把之前那个「稍等」表情撤回——回执只在处理中有意义。
        撤回失败不该影响已经发出的回复，记个 warning 吞掉即可。"""
        if not reaction_id or not message.message_id:
            return
        try:
            await self._loop.run_in_executor(
                None, self._sender.delete_reaction, message.message_id, reaction_id
            )
        except Exception as error:  # noqa: BLE001 - 撤回失败不影响正式回复
            log.warning("撤回「稍等」表情失败：%s", error)

    async def _run(self, message: IncomingMessage, key: GroupConversationKey) -> None:
        try:
            await self._process(message, key)
        except Exception as error:  # noqa: BLE001 - 兜底回执，避免一条失败拖垮队列
            log.exception("处理消息失败")
            await self._reply(message, f"执行失败：{str(error)[:240]}")

    async def _process(self, message: IncomingMessage, key: GroupConversationKey) -> None:
        tag = message_log_tag(message)
        log.info("%s 开始处理", tag)
        if not is_authorized(self._config, message.user_open_id):
            log.info("%s 未授权，拒绝：open_id=%s", tag, message.user_open_id)
            await self._reply(message, "当前用户未授权。请联系管理员把你的 open_id 加入白名单。")
            return

        if message.text.strip() == "/new":
            log.info("%s 指令 /new：重置本群会话", tag)
            self._sessions.reset(key)
            await self._reply(message, "已重置本群会话，下一条消息会创建新的共享 Session。")
            return

        # 已收到回执：直接在原消息下贴「稍等」表情，不管有没有现成会话都一样。
        # 记下 reaction_id，等这一轮跑完（无论成功/失败）在 finally 里把它撤回。
        reaction_id = await self._ack(message)
        try:
            session_id = self._sessions.get(key)
            if not session_id:
                log.info("%s 无现成 Session，创建新的共享 Session", tag)
                session_id = await self._create_session(key)
                log.info("%s 已建 Session=%s", tag, session_id)
            else:
                log.info("%s 命中已有 Session=%s", tag, session_id)

            # 关键：串行模式下发消息时 Session 一定是 idle 的，这一轮独立处理、独立回复。
            actor_input = await self._windowed_input(message)
            log.info("%s 发往方舟运行，input 长度=%d", tag, len(actor_input))
            log.debug("%s 完整 input：\n%s", tag, actor_input)
            try:
                result: RunResult = await self._ark.run(
                    session_id, actor_input, self._config.session_timeout_ms
                )
            except ArkError as error:
                # 兜底：持久化的 session_id 可能已在方舟侧过期/被清（重启后尤甚），
                # 表现为 404 Session 不存在。重建一个新 Session、覆盖落库后原样重跑一次。
                if error.status_code != 404:
                    raise
                log.warning("%s Session 已失效(404)，重建后重试：%s", tag, error)
                session_id = await self._create_session(key)
                log.info("%s 已重建 Session=%s，重跑本轮", tag, session_id)
                result = await self._ark.run(
                    session_id, actor_input, self._config.session_timeout_ms
                )
            await self._reply(message, _result_to_text(result))
            log.info("%s 回复已发出", tag)
        finally:
            # 回复已发出（或本轮已结束）：把「稍等」表情撤回，回执使命完成。
            await self._unack(message, reaction_id)

    async def _create_session(self, key: GroupConversationKey) -> str:
        """建一个新的共享 Session 并落库（覆盖旧映射）。

        Bot-only：不注入任何个人 open_id、不挂个人 Vault/Memory Store。
        既用于首次建会话，也用于 session 失效(404)后的重建。
        """
        session_id = await self._ark.create_session(
            self._config.ark_agent_id,
            self._config.ark_environment_id,
        )
        self._sessions.save(key, session_id)
        return session_id

    async def _windowed_input(self, message: IncomingMessage) -> str:
        """读群历史 → 取「倒数第二次 @bot → 当前」窗口 → 拼成一条 user message。

        群聊才读历史；私聊没有「群上下文」概念，直接带发言人标签的空窗口即可。
        历史读取是 lark-oapi 同步调用，丢到 executor 不阻塞事件循环；读失败降级为无历史。
        """
        if message.chat_type != "group":
            return build_windowed_input(message, [])
        try:
            history = await self._loop.run_in_executor(None, self._sender.list_messages, message)
        except Exception as error:  # noqa: BLE001 - 历史读失败不该拖垮本轮，降级为无上下文
            log.warning("读取群历史失败，本轮不带上下文：%s", error)
            history = []
        return build_windowed_input(message, history)


def _result_to_text(result: RunResult) -> str:
    if result.terminal == "failed":
        raise RuntimeError("Agent Session 执行失败")
    if not result.messages:
        raise RuntimeError("Agent Session 已结束，但没有产生回复")
    return result.messages[-1]


def main() -> None:
    setup_logging(level="INFO")
    config = load_group_bot_config()

    ark = ArkClient(config.ark_api_key, config.ark_base_url)
    sender = FeishuSender(config.feishu_app_id, config.feishu_app_secret)

    loop = asyncio.new_event_loop()
    bot = SerialGroupBot(config, ark, sender, loop)
    threading.Thread(target=loop.run_forever, name="group-bot-loop", daemon=True).start()

    print("客户端串行 Bot 已启动：")
    print(f"- 飞书 App ID：{config.feishu_app_id}")
    print(f"- 群聊共享 Agent ID：{config.ark_agent_id}")
    print("- 策略：同一群/话题串行处理，每人各得独立回复；后到的消息排队。")
    print("在群里 @ 这个 bot 试试（多人先后 @，观察逐条独立回复）。指令：/new 重置本群会话。")
    start_feishu_gateway(config.feishu_app_id, config.feishu_app_secret, bot)


if __name__ == "__main__":
    main()
