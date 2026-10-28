"""topic5 case 的 Gateway:飞书触发词 → 长任务 Runner + HITL 卡片回调。

⚠️ host-only:import ``arkagent.feishu`` / ``arkagent.store`` 及下游 topic5_runner/hitl,
只能在装了 arkagent 框架的 Gateway 宿主进程运行,无法在 MA 云沙箱内独立 import 测试。

只处理 topic5 场景(复盘报告/营销复盘)的入站消息与卡片回调。与 topic6 的差异:
  - 触发词是「复盘报告」「营销复盘」,demo 对清洗后活动明细稳定抽样最多 500 条
  - 交付走飞书文档,**不需要** Miaoda 用户 OAuth / artifact-sync / token-keepalive
  - HC 门是 DAG 人工门 G1/G2/N10/G3/G4/HC7

非触发词消息(以及未鉴权用户)给出用法提示。
"""
from __future__ import annotations

import asyncio
import logging
import re

from arkagent.feishu import IncomingMessage
from arkagent.store import GatewayStore

try:
    from .pipeline_store import PipelineStore
    from .topic5_hitl import Topic5Hitl, Topic5HitlDeps
    from .topic5_runner import (
        Topic5Config as RunnerConfig,
        Topic5Runner,
        Topic5RunnerError,
        normalize_user_text,
        parse_trigger,
    )
    from .config import Topic5Config
except ImportError:  # pragma: no cover - standalone compatibility
    from pipeline_store import PipelineStore
    from topic5_hitl import Topic5Hitl, Topic5HitlDeps
    from topic5_runner import (
        Topic5Config as RunnerConfig,
        Topic5Runner,
        Topic5RunnerError,
        normalize_user_text,
        parse_trigger,
    )
    from config import Topic5Config

log = logging.getLogger("arkagent.case.topic5")
CONTINUE_COMMAND_RE = re.compile(r"^/(?:continue|resume)(?:\s+(.*))?$", re.IGNORECASE)

_USAGE = (
    "当前 Bot 启用 topic5 复盘报告场景。发送「复盘报告 demo」"
    "(G1 自动预填，清洗后活动明细最多 500 条)或"
    "「复盘报告 full」(G1 空表，全量，默认)触发;两者质量门禁相同。"
    "发送 /continue 续跑最近一次任务;发送 /new 可取消当前任务。"
)


class Topic5Gateway:
    """飞书 gateway-like 对象:实现 accept + on_card_action。"""

    def __init__(
        self,
        config: Topic5Config,
        store: GatewayStore,
        runner: Topic5Runner,
        hitl: Topic5Hitl,
        reply,
        loop: asyncio.AbstractEventLoop,
        pipeline_store: PipelineStore | None = None,
    ) -> None:
        self._config = config
        self._store = store
        self._runner = runner
        self._hitl = hitl
        self._reply = reply
        self._loop = loop
        self._pipeline_store = pipeline_store
        self._authorized = set(config.authorized_open_ids)
        self._timeout_task: asyncio.Task | None = None

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

    async def recover_active_jobs(self) -> None:
        """Gateway 启动后把本地活跃任务与 MA 历史事件对齐(只重建 SSE,不发 user.message)。"""
        self._start_timeout_monitor()
        if self._pipeline_store is None:
            return
        active_jobs = self._pipeline_store.list_active_jobs()
        if not active_jobs:
            return
        log.info("topic5 startup recovery begins active_jobs=%s", len(active_jobs))
        results = await self._runner.recover_active_jobs()
        log.info("topic5 startup recovery completed results=%s", results)

    def _start_timeout_monitor(self) -> None:
        if self._timeout_task is not None and not self._timeout_task.done():
            return
        self._timeout_task = asyncio.create_task(self._timeout_monitor())

    async def _timeout_monitor(self) -> None:
        while True:
            try:
                handled = await self._hitl.process_due_g4_timeouts()
                if handled:
                    log.info("topic5 G4 timeout actions handled=%s", handled)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("topic5 G4 timeout monitor failed")
            await asyncio.sleep(15 * 60)

    async def _process(self, message: IncomingMessage) -> None:
        try:
            if self._authorized and message.user_open_id not in self._authorized:
                await self._reply(message.chat_id, "当前用户未授权。请联系管理员把你的 open_id 加入白名单。")
                self._store.complete_event(message.event_id, "completed")
                return

            if message.content_type == "file" and message.resources:
                resource = next(
                    (item for item in message.resources if item.type == "file"),
                    None,
                )
                if resource is None:
                    await self._runner.reply_to_message(
                        message.message_id, "未识别到可下载的文件附件。"
                    )
                    self._store.complete_event(message.event_id, "completed")
                    return
                try:
                    job = await self._runner.attach_activity_file(
                        chat_id=message.chat_id,
                        thread_id=message.thread_id,
                        user_open_id=message.user_open_id,
                        resource=resource,
                    )
                    await self._runner.resume_after_activity_file(job)
                except Topic5RunnerError as error:
                    await self._runner.reply_to_message(
                        message.message_id, f"数据文件未接收：{error}"
                    )
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
                    hint = (
                        "当前话题没有活跃任务。"
                        if message.thread_id
                        else "主会话不绑定具体任务，请进入目标任务话题后发送 /new。"
                    )
                    await self._reply(
                        message.chat_id,
                        f"[/new] {hint}也可在主会话发送「复盘报告 demo」或「复盘报告 full」开新一轮。",
                    )
                else:
                    await self._reply(
                        message.chat_id,
                        f"[/new] 已取消当前任务 job_id={cancelled.job_id}(状态置为 failed:cancelled_by_user)。可再发触发词开新一轮。",
                    )
                self._store.complete_event(message.event_id, "completed")
                return

            continue_match = CONTINUE_COMMAND_RE.fullmatch(text)
            if continue_match:
                continuation_message = (continue_match.group(1) or "").strip() or "继续"
                resumed = await self._runner.continue_job(
                    chat_id=message.chat_id,
                    thread_id=message.thread_id,
                    user_open_id=message.user_open_id,
                    message=continuation_message,
                )
                if resumed is None:
                    hint = (
                        "当前话题没有可续跑的任务。"
                        if message.thread_id
                        else "主会话不绑定具体任务，请进入目标任务话题后发送 /continue。"
                    )
                    await self._reply(
                        message.chat_id,
                        f"[/continue] {hint}若要开新一轮，请在主会话发送「复盘报告 demo」或「复盘报告 full」。",
                    )
                else:
                    message_hint = (
                        "补充消息已注入。"
                        if continue_match.group(1) and continue_match.group(1).strip()
                        else "已注入「继续」。"
                    )
                    await self._reply(
                        message.chat_id,
                        f"[/continue] {message_hint}已重新订阅进度 job_id={resumed.job_id}。若该 Session 已在方舟侧终止,稍后进度卡片会回落为失败。",
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
                await self._reply(message.chat_id, _USAGE)
                self._store.complete_event(message.event_id, "completed")
                return

            try:
                job = await self._runner.start_job(
                    chat_id=message.chat_id,
                    thread_id=message.thread_id,
                    user_open_id=message.user_open_id,
                    mode=mode,
                    user_message=text,
                    user_vault_id=self._config.vault_id,
                    root_message_id=message.message_id,
                )
                log.info("topic5 job started job=%s mode=%s", job.job_id, mode)
            except Topic5RunnerError as error:
                await self._reply(message.chat_id, str(error))
            self._store.complete_event(message.event_id, "completed")
        except Exception as error:  # noqa: BLE001
            self._store.complete_event(message.event_id, "failed")
            reason = str(error)
            await self._reply(message.chat_id, f"执行失败:{reason[:240]}")


def _should_handle_message(message: IncomingMessage) -> bool:
    if message.content_type == "file" and message.resources:
        # 群聊主时间线的普通文件不抢占；Bot 创建的话题内可直接发文件，无需再次 @bot。
        return message.chat_type == "p2p" or bool(message.thread_id)
    if not message.text.strip():
        return False
    return message.chat_type == "p2p" or message.mentioned_bot


def build_gateway(config: Topic5Config, ark, sender, loop: asyncio.AbstractEventLoop):
    """Case 入口:装配 PipelineStore + Runner + HITL,返回 Topic5Gateway。"""
    from arkagent.paths import get_case_paths

    case_paths = get_case_paths("topic5")
    store = GatewayStore(case_paths.database_path)

    pipeline_db = config.pipeline_db_path or None
    pipeline_store = PipelineStore(pipeline_db)
    runner = Topic5Runner(
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
    hitl = Topic5Hitl(Topic5HitlDeps(store=pipeline_store, runner=runner))
    runner.bind_card_sender(hitl)

    async def reply(chat_id: str, text: str) -> None:
        await loop.run_in_executor(None, sender.send_to_chat, chat_id, text)

    return Topic5Gateway(
        config,
        store,
        runner,
        hitl,
        reply,
        loop,
        pipeline_store=pipeline_store,
    )
