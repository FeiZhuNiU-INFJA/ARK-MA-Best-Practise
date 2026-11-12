"""Seedance 创作会话编排。飞书同步调用全部移至 executor。"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import mimetypes
import re
import time
from dataclasses import replace
from pathlib import PurePosixPath

from arkagent.ark import ArkError, ArkRunTimeout, RunResult, event_progress, event_text, result_from_events
from arkagent.feishu import IncomingMessage, normalize_history_item
from arkagent.session_state import is_session_terminal
from arkagent.store import ConversationKey

from .cards import build_card, file_line
from .config import SeedanceConfig
from .progress import ProgressReporter
from .protocol import (
    TURN_CONTRACT, clean_reply, current_events, declared_from_events, declared_from_replies,
    select_files, stage_label, stamp_ms,
)
from .store import SeedanceStore

log = logging.getLogger("seedance.gateway")
HELP = (
    "告诉我你想生成的图片或视频，可注明主体、风格、画幅和时长。\n"
    "参考素材：单聊或创作话题内先发图片／文件，再发要求；群内首次可引用素材并 @Bot。\n"
    "群内 @Bot 开启创作话题，进度集中更新卡片；话题内直接发送即可续聊。\n"
    "/new 开始新创作；/status 查看状态；/files 获取最终交付物；/help 查看帮助。"
)


def conversation(message: IncomingMessage) -> ConversationKey:
    return ConversationKey(message.tenant_key, message.chat_id, message.thread_id, message.user_open_id)


def safe_name(name: str) -> str:
    basename = PurePosixPath(name.replace("\\", "/")).name
    return re.sub(r"[^\w.\-]", "_", basename).strip(".")[:160] or "attachment"


def display_reply(text: str) -> str:
    # 沙箱链接在飞书不可访问，实际文件由 Files API 回传。
    return re.sub(
        r"!?\[([^\]]*)\]\((?:sandbox:)?/(?:mnt/session|workspace)/outputs/[^)]+\)",
        lambda match: match.group(1) or "交付文件",
        clean_reply(text),
    )


class SeedanceGateway:
    def __init__(self, config: SeedanceConfig, ark, sender, loop, store=None):
        self.config, self.ark, self.sender, self.loop = config, ark, sender, loop
        self.store = store if store is not None else SeedanceStore(config.database_path)
        self._locks: dict[str, asyncio.Lock] = {}
        self._delivery_locks: dict[str, asyncio.Lock] = {}
        self._card_locks: dict[str, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task] = set()
        self._observers: dict[str, asyncio.Task] = {}
        self.progress_interval = 15
        self.observer_interval = 15

    def accept(self, message: IncomingMessage) -> None:
        if not self._allowed(message):
            return
        self.loop.call_soon_threadsafe(self._schedule, message)

    def _allowed(self, message: IncomingMessage) -> bool:
        if message.chat_type == "group" and not message.mentioned_bot:
            if not self.store.topic(message.chat_id, message.root_id, message.thread_id):
                return False
        allowed = self.config.authorized_open_ids
        if allowed and message.user_open_id not in allowed:
            return False
        if not message.user_open_id or not message.message_id:
            return False
        return True

    def _normalize_topic(self, message: IncomingMessage) -> IncomingMessage:
        if message.chat_type != "group":
            return message
        root = self.store.topic(message.chat_id, message.root_id, message.thread_id)
        root = root or message.root_id or message.thread_id or message.message_id
        self.store.save_topic(message.chat_id, root, message.root_id, message.thread_id)
        return replace(message, thread_id=root)

    def _schedule(self, message: IncomingMessage) -> None:
        task = self.loop.create_task(self.handle(message))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _text(self, message: IncomingMessage, text: str) -> None:
        in_thread = message.chat_type == "group" or bool(message.thread_id)
        method = self.sender.reply_in_thread if in_thread else self.sender.reply
        actual = await asyncio.to_thread(method, message.message_id, text)
        if in_thread and isinstance(actual, str) and actual:
            self.store.save_topic(message.chat_id, message.thread_id, actual)

    async def _update(self, message, *, reset=False, quiet=False, file=None, session_id=None,
                      expected_turn=None, keep_files=None, **changes) -> None:
        key = conversation(message)
        lock = self._card_locks.setdefault(self.store.conversation_key(key), asyncio.Lock())
        async with lock:
            # /files 可以和创作并行；旧 Session 的产物不能覆盖新创作卡片。
            if session_id and self.store.get_session(key) != session_id:
                return
            state = self.store.card(key)
            if expected_turn and state.get("turn_id") != expected_turn:
                return
            if (
                session_id and state.get("phase") in ("running", "delivering")
                and state.get("attached_session") != session_id
            ):
                return
            if reset:
                state = {"card_id": state.get("card_id"), "files": []}
            state.update(changes)
            if session_id:
                state["attached_session"] = session_id
            if keep_files is not None:
                state["files"] = [
                    item for item in state.get("files", []) if item["name"] in keep_files
                ]
            if file:
                files = state.setdefault("files", [])
                files[:] = [
                    item for item in files
                    if item["id"] != file["id"] and item["name"] != file["name"]
                ]
                files.append(file)
            self.store.save_card(key, state)
            try:
                card = build_card(state, group=message.chat_type == "group")
                if state.get("card_id"):
                    await asyncio.to_thread(self.sender.patch_interactive_card, state["card_id"], card)
                elif not state.get("create_failed"):
                    if message.chat_type == "group" or message.thread_id:
                        card_id, actual = await asyncio.to_thread(
                            self.sender.send_interactive_card_in_thread, message.message_id, card
                        )
                        if actual:
                            self.store.save_topic(message.chat_id, message.thread_id, actual)
                    else:
                        card_id = await asyncio.to_thread(self.sender.send_interactive_card, message.chat_id, card)
                    if not card_id:
                        raise RuntimeError("飞书未返回卡片消息 ID")
                    state["card_id"] = card_id
                else:
                    raise RuntimeError("本轮卡片创建失败")
                state.pop("patch_warning", None)
                self.store.save_card(key, state)
                if file and file.get("url") and not any(
                    file_line(file) in element.get("content", "")
                    for element in card["body"]["elements"]
                ):
                    # 超出卡片条数/字节预算的下载链接仍单独投递到话题，避免丢失交付。
                    await self._text(message, file_line(file))
            except Exception as error:
                log.warning("Seedance 卡片更新降级 type=%s", type(error).__name__)
                if not state.get("card_id"):
                    state["create_failed"] = True
                if quiet:
                    self.store.save_card(key, state)
                    return
                warn = not state.get("patch_warning")
                state["patch_warning"] = True
                self.store.save_card(key, state)
                text = "\n\n".join(str(changes[field]) for field in ("detail", "result", "notice") if changes.get(field))
                if file and file.get("url"):
                    text = file_line(file)
                if warn:
                    text = ("卡片显示未完成，请检查 im:message:update 权限并发布应用版本。\n" + text)
                if text:
                    try:
                        await self._text(message, text)
                    except Exception:
                        log.warning("Seedance 卡片降级提示投递失败")

    async def handle(self, message: IncomingMessage) -> None:
        if not self._allowed(message):
            return
        event_id = message.event_id or message.message_id
        if not self.store.claim_event(event_id):
            return
        message = self._normalize_topic(message)
        key = conversation(message)
        command = re.sub(r"^(?:@\S+\s+)+", "", message.text.strip()).strip().lower()
        try:
            # 查询命令不排在长时生成任务之后，便于超时／断流后回查。
            if command in ("/status", "/files", "/help"):
                await self._command(message, key, command)
            else:
                lock = self._locks.setdefault(self.store.conversation_key(key), asyncio.Lock())
                async with lock:
                    await self._process(message, key, command)
            self.store.complete_event(event_id, "done")
        except Exception as error:
            self.store.complete_event(event_id, "failed")
            # 不打印异常 body / 签名 URL / 凭据。
            log.error("Seedance 处理失败 type=%s status=%s", type(error).__name__, getattr(error, "status_code", None))
            detail = str(error) if isinstance(error, ValueError) else (
                "方舟权限或模型配置有误，请检查 Seedance、模型及工具服务是否已开通。"
                if getattr(error, "status_code", None) in (400, 403)
                else "处理未完成，可用 /status 查看状态、/files 获取已生成的产物。"
            )
            try:
                if command in ("/status", "/files", "/help"):
                    await self._update(message, notice=detail)
                else:
                    await self._update(message, phase="failed", detail=detail)
            except Exception:
                log.error("Seedance 错误提示投递失败")

    async def _command(self, message, key, command) -> None:
        if command == "/help":
            await self._update(message, notice=HELP)
            return
        session_id = self.store.get_session(key)
        if not session_id:
            await self._update(message, notice="当前还没有创作会话。\n" + HELP)
        elif command == "/status":
            status = (await self.ark.get_session(session_id)).get("status") or "unknown"
            await self._refresh_history(message, session_id)
            label = {"running": "正在创作", "idle": "等待下一条指令", "failed": "执行失败",
                     "terminated": "已终止"}.get(status, status)
            changes = {"notice": f"当前创作：{label}。"}
            phase = self.store.card(key).get("phase")
            if phase == "timed_out" and not self._busy(key):
                if status == "running":
                    changes.update(detail="本地等待已超时，云端仍在创作；任务未被取消。")
                elif status == "idle":
                    changes.update(
                        phase="done", terminal="idle",
                        detail="云端创作已结束，可用 /files 获取最终交付物。",
                    )
                elif status in ("failed", "terminated"):
                    changes.update(
                        phase="failed", terminal="failed",
                        detail=f"方舟确认本次创作{label}，可用 /files 获取已生成的产物。",
                    )
            elif phase == "failed" and not self._busy(key):
                changes.update(
                    phase={"running": "running", "idle": "waiting"}.get(status, "failed"),
                    detail=f"当前创作：{label}。",
                )
            await self._update(message, session_id=session_id, **changes)
            self._track_if_active(message, session_id)
        else:
            await self._refresh_history(message, session_id)
            state = self.store.card(key)
            turn_id = state.get("turn_id")
            names = state.get("expected")
            if names is None:
                await self._update(
                    message, session_id=session_id, expected_turn=turn_id,
                    keep_files=set(),
                    notice="尚未确认最终交付物；检索素材和中间文件不会回传。请稍后再用 /files。",
                )
                self._track_if_active(message, session_id)
                return
            count = await self._deliver(
                message, session_id, force=True, expected=set(names), turn_id=turn_id,
            )
            if not count:
                await self._update(
                    message, session_id=session_id, expected_turn=turn_id,
                    notice="最终交付文件尚未登记完成，请稍后再用 /files。" if names
                    else "本轮没有需要回传的最终交付文件。",
                )
            elif not self._busy(key):
                state = self.store.card(key)
                complete = state.get("terminal") == "idle" and not state.get("missing")
                await self._update(
                    message, session_id=session_id, expected_turn=turn_id,
                    **({"phase": "done", "detail": "已重新获取最终交付物。", "notice": ""}
                       if complete else {"notice": "已获取最终交付物；可用 /status 查看创作状态。"}),
                )
            self._track_if_active(message, session_id)

    def _busy(self, key: ConversationKey) -> bool:
        lock = self._locks.get(self.store.conversation_key(key))
        return bool(lock and lock.locked())

    async def _process(self, message, key, command) -> None:
        if command == "/new":
            self.store.reset_session(key)
            await self._update(message, reset=True, phase="waiting", detail="已开始新的创作，请发送需求或参考素材。")
            return
        if command.startswith("/"):
            await self._update(message, notice=HELP)
            return
        refs = list(message.resources)
        if message.reply_to_message_id:
            item = await asyncio.to_thread(self.sender.get_message, message.reply_to_message_id)
            if item and item.get("chat_id") == message.chat_id:
                quoted = normalize_history_item(item, "")
                if quoted:
                    refs.extend(quoted.resources)
        for ref in refs:
            self.store.add_input(key, replace(ref, message_id=ref.message_id or message.message_id))
        if not message.text.strip():
            await self._update(message, notice="已收到参考素材，请继续发送创作要求。")
            return

        old_session = self.store.get_session(key)
        old_outputs: list[dict] = []
        if old_session:
            try:
                session = await self.ark.get_session(old_session)
            except ArkError as error:
                if error.status_code != 404:
                    raise
                session = {"status": "terminated"}
                old_session = None
            if session.get("status") == "running":
                await self._update(message, notice="上一次创作仍在运行，请稍后继续；可用 /status 或 /files 回查。")
                return
            if is_session_terminal(session.get("status")):
                old_session = None

        inputs = self.store.inputs(key)
        if len(inputs) > 100:
            raise ValueError("参考素材超过单 Session 的 100 个文件上限，请用 /new 开始新创作。")
        input_keys = {item["key"] for item in inputs}
        needs_new = not old_session or self.store.snapshot(old_session) != input_keys
        if old_session and needs_new:
            await self._refresh_history(message, old_session)
            old_outputs = select_files(
                await self.ark.list_agent_files(old_session),
                set(self.store.card(key).get("expected") or ()),
            )
        if len(inputs) + len(old_outputs) > 100:
            raise ValueError("参考素材与上轮产物超过 100 个文件，请用 /new 缩小创作范围。")

        turn_id = message.message_id
        previous_user_id = ""
        if old_session and not needs_new:
            previous_user_id = next((
                str(event.get("id") or "")
                for event in reversed(await self.ark.list_session_events(old_session))
                if event.get("type") == "user.message"
            ), "")
        await self._update(
            message, reset=True, phase="running", request=message.text,
            detail="正在准备创作，完成后会回传最终交付物。",
            **{"turn_id": turn_id, "previous_user_id": previous_user_id, "route": {
                field: getattr(message, field) for field in (
                    "tenant_key", "chat_id", "thread_id", "user_open_id", "user_name",
                    "message_id", "chat_type",
                )
            }},
        )
        resources: list[dict] = []
        for item in inputs:
            if not item["file_id"]:
                ref = item["ref"]
                data = await asyncio.to_thread(
                    self.sender.download_resource, ref.message_id, ref.file_key, ref.type
                )
                if not data or len(data) > self.config.max_input_bytes:
                    raise ValueError("参考素材为空或超过 SEEDANCE_MAX_INPUT_BYTES 配置上限。")
                name = safe_name(ref.file_name)
                mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
                item["file_id"] = await self.ark.upload_file(name, mime, data)
                digest = hashlib.sha256(item["key"].encode()).hexdigest()[:12]
                item["mount_path"] = f"/mnt/session/uploads/{digest}-{name}"
                self.store.save_input(key, item["key"], item["file_id"], item["mount_path"])
            resources.append({"type": "file", "file_id": item["file_id"], "mount_path": item["mount_path"]})
        for index, item in enumerate(old_outputs):
            name = safe_name(str(item.get("filename") or item.get("name") or item["id"]))
            resources.append({
                "type": "file", "file_id": item["id"],
                "mount_path": f"/mnt/session/uploads/previous-{index + 1}-{name}",
            })
        session_id = old_session
        if needs_new:
            session_id = await self.ark.create_session(
                self.config.agent_id, self.config.environment_id, resources=resources,
                title=f"视觉创作 · {message.user_name or '飞书用户'}",
            )
            self.store.save_session(key, session_id, self.config.agent_id)
            self.store.save_snapshot(session_id, input_keys)
        started_at = int(time.time() * 1000)
        await self._update(
            message, attached_session=session_id, started_at=started_at,
            progress_at=started_at, stage="正在准备创作。", quiet=True, expected_turn=turn_id,
        )
        prompt = message.text
        if needs_new and self.store.context(key):
            prompt += "\n\n最近创作上下文（供继续创作）：\n" + self.store.context(key)
        if resources:
            prompt += "\n\n已挂载参考素材（只读）：\n" + "\n".join(
                resource["mount_path"] for resource in resources
            )
        async def progress_update(label: str) -> None:
            await self._update(
                message, session_id=session_id, expected_turn=turn_id, quiet=True,
                progress_at=int(time.time() * 1000),
                **({"stage": label, "detail": label} if label else {}),
            )

        reporter = ProgressReporter(progress_update, interval=self.progress_interval)
        try:
            try:
                result = await self.ark.run(
                    session_id, prompt, self.config.session_timeout_ms,
                    on_progress=reporter.feed, system_message=TURN_CONTRACT,
                )
            finally:
                await reporter.stop()
        except ArkRunTimeout:
            log.warning(
                "Seedance 等待超时 session=%s timeout_ms=%s",
                session_id, self.config.session_timeout_ms,
            )
            minutes = self.config.session_timeout_ms / 60000
            await self._update(
                message, phase="timed_out", session_id=session_id, expected_turn=turn_id,
                detail=f"本地等待已超过 {minutes:g} 分钟，尚未确认云端任务结束；任务未被取消。",
                notice="将继续跟踪云端进度，完成后回传最终交付物。可用 /status 回查，无需重复生成。",
            )
            self._track_if_active(message, session_id)
            return
        await self._complete(message, session_id, turn_id, result)

    def _current(self, message, session_id, turn_id=None) -> bool:
        key = conversation(message)
        state = self.store.card(key)
        return (
            self.store.get_session(key) == session_id
            and (not turn_id or state.get("turn_id") == turn_id)
            and state.get("attached_session", session_id) == session_id
        )

    async def _complete(self, message, session_id, turn_id, result) -> None:
        key = conversation(message)
        if not self._current(message, session_id, turn_id):
            return
        if turn_id and self.store.card(key).get("finished_turn") == turn_id:
            return
        reply = result.messages[-1] if result.messages else ""
        if not turn_id or self.store.card(key).get("recorded_turn") != turn_id:
            self.store.save_turn(key, message.text, reply)
        expected = declared_from_replies(result.messages, final=True)
        if expected is None and not reply:
            expected = self.store.card(key).get("expected")
        await self._update(
            message, phase="delivering", session_id=session_id, expected_turn=turn_id,
            result=display_reply(reply), terminal=result.terminal,
            expected=sorted(expected) if expected is not None else None,
            detail="正在查询并回传最终交付物。",
            progress_at=int(time.time() * 1000),
            recorded_turn=turn_id,
            notice="" if result.terminal == "idle" else "本次创作未全部完成，正在查询最终交付物。",
        )
        count = await self._deliver(
            message, session_id, expected=expected, wait=True, turn_id=turn_id,
        )
        if expected and not count:
            if self.store.card(key).get("missing"):
                await self._update(
                    message, session_id=session_id, expected_turn=turn_id,
                    notice="产物尚未登记完成，可稍后用 /files 获取，无需重复生成。",
                )
        elif not reply and not count:
            await self._update(
                message, session_id=session_id, expected_turn=turn_id,
                notice="暂未收到创作结果，可用 /status 和 /files 回查。",
            )
        state = self.store.card(key)
        failed = result.terminal != "idle"
        partial = bool(state.get("missing")) or (not reply and not count)
        await self._update(
            message, session_id=session_id, expected_turn=turn_id, finished_turn=turn_id,
            progress_at=int(time.time() * 1000),
            phase="failed" if failed else ("partial" if partial else "done"),
            detail=(
                "云端创作执行失败，可用 /files 获取已生成的产物。"
                if failed else (
                    "可用 /status 或 /files 回查，或继续发送修改要求。"
                    if partial else "创作结果和可下载产物已整理。"
                )
            ),
        )

    async def _refresh_history(self, message, session_id):
        key = conversation(message)
        state = self.store.card(key)
        turn_id = state.get("turn_id")
        if not self._current(message, session_id, turn_id):
            return [], None
        events = current_events(
            await self.ark.list_session_events(session_id), state.get("started_at") or 0,
            state.get("previous_user_id") or "",
        )
        if not events or not self._current(message, session_id, turn_id):
            return [], None
        result = result_from_events(events, 0)
        names = declared_from_events(events, final=result is not None)
        labels = [stage_label(event_progress(event) or "") for event in events]
        stage = next((label for label in reversed(labels) if label), "")
        changes = {"progress_at": int(time.time() * 1000)}
        if stage and (not self._busy(key) or state.get("phase") == "timed_out"):
            changes["stage"] = stage
        if names is not None:
            changes.update(
                expected=sorted(names), keep_files=names,
            )
        elif state.get("expected") is None:
            changes["keep_files"] = set()
        if result:
            changes["terminal"] = result.terminal
            if names is None:
                changes.update(expected=None, keep_files=set())
        await self._update(
            message, session_id=session_id, expected_turn=turn_id, quiet=True, **changes,
        )
        return events, result

    def _track_if_active(self, message, session_id) -> None:
        key = conversation(message)
        state = self.store.card(key)
        token = f"{self.store.conversation_key(key)}:{session_id}:{state.get('turn_id')}"
        if (
            state.get("phase") not in ("running", "timed_out", "delivering")
            or self._busy(key) and state.get("phase") != "timed_out"
            or token in self._observers
            or not self._current(message, session_id, state.get("turn_id"))
        ):
            return
        task = self.loop.create_task(self._observe(
            replace(message, text=state.get("request") or message.text),
            session_id, state.get("turn_id"),
        ))
        self._observers[token] = task
        self._tasks.add(task)

        def done(finished):
            self._tasks.discard(finished)
            if self._observers.get(token) is finished:
                self._observers.pop(token, None)
            if not finished.cancelled() and finished.exception():
                log.warning("Seedance 云端跟踪结束 type=%s", type(finished.exception()).__name__)
        task.add_done_callback(done)

    async def _observe(self, message, session_id, turn_id) -> None:
        key = conversation(message)
        while self._current(message, session_id, turn_id):
            try:
                events, result = await self._refresh_history(message, session_id)
                if not result:
                    status = (await self.ark.get_session(session_id)).get("status")
                    if events and status in ("idle", "failed", "terminated"):
                        result = RunResult(
                            terminal="idle" if status == "idle" else "failed",
                            messages=[event_text(event) for event in events if event.get("type") == "agent.message"],
                        )
                if result:
                    lock = self._locks.setdefault(self.store.conversation_key(key), asyncio.Lock())
                    async with lock:
                        await self._complete(message, session_id, turn_id, result)
                    return
                state = self.store.card(key)
                if state.get("phase") not in ("running", "timed_out", "delivering"):
                    return
                if not events:
                    await self._update(
                        message, session_id=session_id, expected_turn=turn_id, quiet=True,
                        progress_at=int(time.time() * 1000),
                    )
            except Exception as error:
                log.warning("Seedance 云端进度回查暂未完成 type=%s", type(error).__name__)
            await asyncio.sleep(self.observer_interval)

    async def recover_active_jobs(self) -> None:
        """启动时只读恢复旧回合；不再次发送创作请求。"""
        for key, session_id, state in self.store.active_cards():
            route = state.get("route") or {
                "tenant_key": key.tenant_key, "chat_id": key.chat_id, "thread_id": key.thread_id,
                "user_open_id": key.user_open_id, "user_name": "",
                "chat_type": "group" if key.thread_id else "p2p",
                "message_id": key.thread_id or state.get("card_id"),
            }
            if not route.get("message_id"):
                continue
            message = IncomingMessage(
                **route, event_id="", text=state.get("request") or "", mentioned_bot=True,
            )
            if not state.get("turn_id"):
                try:
                    events = current_events(await self.ark.list_session_events(session_id))
                    if state.get("phase") == "failed":
                        status = (await self.ark.get_session(session_id)).get("status")
                        result = result_from_events(events, 0)
                        if status != "running" and not (
                            status == "idle" and result and result.terminal == "idle"
                        ):
                            continue
                        await self._update(
                            message, session_id=session_id, quiet=True, phase="timed_out",
                            terminal=None, notice="", keep_files=set(),
                            detail="正在按云端实际状态继续跟踪创作进度；无需重复提交需求。",
                        )
                except Exception as error:
                    log.warning("Seedance 旧回合暂未恢复 type=%s", type(error).__name__)
                    continue
                if not events:
                    continue
                turn_id = str(events[0].get("id") or route["message_id"])
                await self._update(
                    message, session_id=session_id, quiet=True,
                    **{"turn_id": turn_id, "attached_session": session_id,
                       "started_at": stamp_ms(events[0]) or 0, "route": route},
                )
            self._track_if_active(message, session_id)

    async def _deliver(self, message, session_id, *, expected=None, wait=False,
                       force=False, turn_id=None) -> int:
        if not expected or not self._current(message, session_id, turn_id):
            return 0
        lock = self._delivery_locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            sent = 0
            seen: set[str] = set()
            previous: set[str] = set()
            attempts = self.config.artifact_poll_attempts if wait else 1
            for attempt in range(attempts):
                files = await self.ark.list_agent_files(session_id)
                ready = [
                    item for item in select_files(files, set(expected)) if item.get("download_url")
                ]
                names = {str(item.get("filename") or item.get("name")) for item in ready}
                for item in ready:
                    if not self._current(message, session_id, turn_id):
                        return sent
                    file_id = str(item["id"])
                    if file_id in seen:
                        continue
                    if not force and self.store.delivered(session_id, file_id):
                        existing = next((
                            file for file in self.store.card(conversation(message)).get("files", [])
                            if file["id"] == file_id
                        ), None)
                        if not existing:
                            await self._update(
                                message, session_id=session_id, expected_turn=turn_id,
                                file={"id": file_id, "name": str(item.get("filename") or item.get("name")),
                                      "url": str(item["download_url"])},
                            )
                        seen.add(file_id)
                        continue
                    delivered = await self._send_artifact(message, item)
                    await self._update(
                        message, file=delivered, session_id=session_id, expected_turn=turn_id,
                    )
                    self.store.mark_delivered(session_id, file_id)
                    seen.add(file_id)
                    sent += 1
                previous = names
                if expected and set(expected).issubset(names):
                    break
                if attempt + 1 < attempts:
                    await asyncio.sleep(self.config.artifact_poll_interval_ms / 1000)
            missing = set(expected or ()) - (previous or set())
            if missing and sent:
                await self._update(
                    message, notice="部分文件仍在登记中，请稍后用 /files 获取剩余产物。",
                    session_id=session_id, expected_turn=turn_id,
                )
            await self._update(
                message, missing=sorted(missing), session_id=session_id, expected_turn=turn_id, quiet=True,
            )
            return sent

    async def _send_artifact(self, message, item) -> dict:
        name = str(item.get("filename") or item.get("name") or item["id"])
        url = str(item["download_url"])
        try:
            size = int(item.get("bytes") or item.get("size") or 0)
            if size > self.config.max_output_bytes:
                raise ValueError("文件超过回传上限")
            data = await self.ark.download_file(url, max_bytes=self.config.max_output_bytes, timeout=120)
            if not data:
                raise ValueError("产物为空")
            is_image = PurePosixPath(name).suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
            if is_image and len(data) <= 10 * 1024 * 1024:
                image_key = await asyncio.to_thread(self.sender.upload_image, data)
                await asyncio.to_thread(
                    self.sender.reply_image, message.message_id, image_key,
                    message.chat_type == "group" or bool(message.thread_id),
                )
            else:
                file_type = "mp4" if name.lower().endswith(".mp4") else "stream"
                file_key = await asyncio.to_thread(self.sender.upload_file, name, data, file_type)
                await asyncio.to_thread(
                    self.sender.reply_file, message.message_id, file_key,
                    message.chat_type == "group" or bool(message.thread_id),
                )
            return {"id": str(item["id"]), "name": name}
        except Exception as error:
            log.warning("Seedance 文件回传降级 type=%s", type(error).__name__)
            if not url.startswith(("https://", "http://")):
                raise ValueError("Files API 未返回有效下载地址")
            return {"id": str(item["id"]), "name": name, "url": url}


def build_gateway(config, ark, sender, loop: asyncio.AbstractEventLoop):
    return SeedanceGateway(config, ark, sender, loop)
