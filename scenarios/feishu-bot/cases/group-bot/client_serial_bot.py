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
    PreparedAttachment,
    SqliteSessionMap,
    THREAD_CONTEXT_BEFORE,
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
    should_handle,
    to_group_key,
)

# shared 已把仓库根加入 sys.path，这里能 import 到主包基础设施。
from arkagent.ark import ArkClient, ArkError, RunResult
from arkagent.feishu import (
    FeishuSender,
    HistoryMessage,
    IncomingMessage,
    QuotedMessage,
    ResourceRef,
    start_feishu_gateway,
)
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

    async def _reply(
        self, message: IncomingMessage, text: str, roster: Optional[dict] = None
    ) -> None:
        # 群里回复到原消息（reply），私聊直接发到会话。lark-oapi 是同步调用，丢到 executor。
        # roster（群成员名册）非空时，正文里的 @人名 会被渲染成可点击 <at> 提及。
        if message.chat_type == "group" and message.message_id:
            await self._loop.run_in_executor(None, self._sender.reply, message.message_id, text, roster)
        else:
            await self._loop.run_in_executor(None, self._sender.send_to_chat, message.chat_id, text, roster)

    async def _chat_roster(self, message: IncomingMessage) -> dict:
        """群成员名册（名字→open_id），供把 Agent 回复里 @人名 渲成可点击提及用。

        仅群聊需要（私聊没有 @ 别人的语义，返回空）。FeishuSender.chat_roster 自带 TTL 缓存 +
        同名消歧，拉取失败时返回空名册；这里再兜一层异常，名册问题绝不该拖垮回复。"""
        if message.chat_type != "group" or not message.chat_id:
            return {}
        try:
            return await self._loop.run_in_executor(None, self._sender.chat_roster, message.chat_id)
        except Exception as error:  # noqa: BLE001 - 名册拉取失败退回不 @，不影响回复
            log.warning("获取群成员名册失败，本条回复不 @：%s", error)
            return {}

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
        except Exception:  # noqa: BLE001 - 兜底回执，避免一条失败拖垮队列
            # 详情（含方舟 file_url 超时等内部原因）进日志；回群里的用户只给友好提示，不外泄内部细节。
            log.exception("处理消息失败")
            await self._reply(message, "执行失败，请稍后重试；若持续失败可 @我 发 /new 重置会话。")

    async def _process(self, message: IncomingMessage, key: GroupConversationKey) -> None:
        tag = message_log_tag(message)
        log.info("%s 开始处理", tag)
        if not is_authorized(self._config, message.user_open_id):
            log.info("%s 未授权，拒绝：open_id=%s", tag, message.user_open_id)
            await self._reply(message, "当前用户未授权。请联系管理员把你的 open_id 加入白名单。")
            return

        if is_reset_command(message.text):
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
                session_id = await self._create_session(key, message)
                log.info("%s 已建 Session=%s", tag, session_id)
            else:
                log.info("%s 命中已有 Session=%s", tag, session_id)

            # 关键：串行模式下发消息时 Session 一定是 idle 的，这一轮独立处理、独立回复。
            # 先把本轮上下文读齐（群历史窗口 + 引用链 + 话题前情），再据此收集本轮要挂的附件
            # ——文件常是单独一条消息发的、之后才 @bot，附件得从落进窗口的历史里一并收出来。
            # 多模态：下载附件并上传方舟拿 file_id（与 Session 无关，只做一次），
            # 再挂到本 Session 的 /mnt/session/uploads/；正文里告诉模型文件挂在哪、请去读。
            history, quote_chain, thread_context = await self._read_context(message)
            prepared, notices = await self._prepare_attachments(
                message, collect_round_resources(message, history, thread_context)
            )
            await self._mount_attachments(session_id, prepared)
            actor_input = build_windowed_input(
                message, history, quote_chain, thread_context, prepared=prepared, notices=notices
            )
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
                session_id = await self._create_session(key, message)
                log.info("%s 已重建 Session=%s，重跑本轮", tag, session_id)
                # 附件已上传（file_id 与 Session 无关、仍有效），只需重新挂到新 Session。
                await self._mount_attachments(session_id, prepared)
                result = await self._ark.run(
                    session_id, actor_input, self._config.session_timeout_ms
                )
            # Agent 回复里可能点名群成员（「@张三 请跟进」）：取本群名册，把 @人名 渲成可点击提及。
            roster = await self._chat_roster(message)
            await self._reply(message, _result_to_text(result), roster)
            log.info("%s 回复已发出", tag)
        finally:
            # 回复已发出（或本轮已结束）：把「稍等」表情撤回，回执使命完成。
            await self._unack(message, reaction_id)

    async def _create_session(self, key: GroupConversationKey, message: IncomingMessage) -> str:
        """建一个新的共享 Session 并落库（覆盖旧映射）。

        Bot-only：不注入任何个人 open_id、不挂个人 Vault/Memory Store。
        lark-cli（Bot 身份）：配了 Vault 才挂——vault 里是 LARKSUITE_CLI_APP_SECRET，
        env_overrides 补当前群/话题定位（$FEISHU_CHAT_ID 等）。没配则退回纯对话。
        既用于首次建会话，也用于 session 失效(404)后的重建。
        """
        session_id = await self._ark.create_session(
            self._config.ark_agent_id,
            self._config.ark_environment_id,
            vault_ids=[self._config.lark_vault_id] if lark_cli_enabled(self._config) else None,
            env_overrides=build_lark_session_env(message) if lark_cli_enabled(self._config) else None,
        )
        self._sessions.save(key, session_id)
        return session_id

    async def _read_context(
        self, message: IncomingMessage
    ) -> tuple[list[HistoryMessage], list[QuotedMessage], list[HistoryMessage]]:
        """读本轮上下文：群历史窗口 + 被引用消息链 + 话题前情。返回三段供拼正文/收附件复用。

        群聊才读历史；私聊没有「群上下文」概念，直接空窗口即可。
        历史读取是 lark-oapi 同步调用，丢到 executor 不阻塞事件循环；读失败降级为无历史。
        引用链同理：若这条消息显式引用了别的消息（reply_to_message_id 非空），沿父链最多
        回溯 MAX_QUOTE_DEPTH 层读出来，读失败（撤回/无权限/跨会话）降级为无引用。
        话题前情：话题里 @bot 时，thread 容器读不到「发起话题的根消息 + 根之前几条主时间线」，
        单独补读（load_thread_context），读失败降级为无前情。
        这三段既拼进正文（build_windowed_input），也用来收本轮要挂的附件（collect_round_resources）
        ——保证「进正文的转录范围」与「挂进 Session 的附件范围」一致。
        """
        if message.chat_type != "group":
            return [], [], []
        try:
            history = await self._loop.run_in_executor(None, self._sender.list_messages, message)
        except Exception as error:  # noqa: BLE001 - 历史读失败不该拖垮本轮，降级为无上下文
            log.warning("读取群历史失败，本轮不带上下文：%s", error)
            history = []
        quote_chain = await self._quote_chain(message)
        thread_context = await self._thread_context(message)
        return history, quote_chain, thread_context

    async def _prepare_attachments(
        self, message: IncomingMessage, resources: list[ResourceRef]
    ) -> tuple[list[PreparedAttachment], list[str]]:
        """把本轮附件逐个「下载 →（内联小文本 / 上传方舟拿 file_id）」，产出待挂载结果 + 降级提示。

        resources 由 collect_round_resources 收齐（触发消息 + 落进窗口的历史消息 + 话题前情里的
        附件，按 file_key 去重），下载按各 ref.message_id 定位所属消息——文件常是单独一条消息发的、
        之后才 @bot。无附件、或 GROUP_BOT_MULTIMODAL 关闭时直接返回空——关闭时不下载不上传，带附件
        的消息按纯文本处理（正文里只留一句「[附件已忽略]」占位提示）。下载/上传都是同步调用（前者
        lark-oapi、后者 httpx.post multipart），丢到 executor 不阻塞事件循环，逐个附件降级由
        shared.prepare_attachments 内部处理，这里只注入两个 IO 能力。

        去重·文件缓存层：把 store 的 get_attachment/save_attachment 作为 lookup/save 回调注入——
        同一 file_key 上传过就复用旧 file_id，跳过下载 + 上传（跨 session 也生效）。store 若没实现
        这两个方法（自定义替身）则不传，退回每次都下载上传的老行为。
        """
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

        lookup = getattr(self._sessions, "get_attachment", None)
        save = getattr(self._sessions, "save_attachment", None)
        return await prepare_attachments(
            message, _download, _upload,
            lookup_file_id=lookup, save_file_id=save, resources=resources,
        )

    async def _mount_attachments(
        self, session_id: str, prepared: list[PreparedAttachment]
    ) -> None:
        """把已上传的附件（有 file_id 的）逐个挂到本 Session 的 /mnt/session/uploads/{mount_path}。

        内联文本（inline_text 非空、无 file_id）不挂载，直接进正文，跳过。单个挂载失败记 warning
        但不抛——正文里仍会列出该路径，模型读不到时会自行说明，不拖垮本轮其余附件与回复。

        去重·挂载记录层：同一资源（file_key）已挂到本 session 就跳过——一个文件在一个会话里
        只需挂一次，之后每轮引用同一路径即可，别反复 add_session_file。store 未实现去重方法
        （自定义替身）时退回每轮都挂的老行为。
        """
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
            except Exception as error:  # noqa: BLE001 - 单个挂载失败不该拖垮本轮
                log.warning("挂载附件「%s」到 Session 失败：%s", item.name, error)

    async def _quote_chain(self, message: IncomingMessage) -> list[QuotedMessage]:
        """读被引用消息链（同步 lark-oapi 调用，丢 executor）。无引用/读失败降级为空。"""
        if not message.reply_to_message_id:
            return []
        try:
            return await self._loop.run_in_executor(None, self._sender.load_quote_chain, message)
        except Exception as error:  # noqa: BLE001 - 引用读失败不该拖垮本轮，降级为无引用
            log.warning("读取被引用消息失败，本轮不带引用：%s", error)
            return []

    async def _thread_context(self, message: IncomingMessage) -> list[HistoryMessage]:
        """读话题前情（根消息 + 根之前 N 条主时间线）。非话题/读失败降级为空。"""
        if not message.root_id:
            return []
        try:
            return await self._loop.run_in_executor(
                None, self._sender.load_thread_context, message, THREAD_CONTEXT_BEFORE
            )
        except Exception as error:  # noqa: BLE001 - 话题前情读失败不该拖垮本轮，降级为无前情
            log.warning("读取话题前情失败，本轮不带话题前情：%s", error)
            return []


def _result_to_text(result: RunResult) -> str:
    if result.terminal == "failed":
        # 带上方舟给的失败摘要（如 file_url 超时）——它会进 _run 的异常日志，便于排查；
        # 回给群里用户的文案由 _run 兜底成固定友好话术，不直接外泄这里的内部细节。
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
