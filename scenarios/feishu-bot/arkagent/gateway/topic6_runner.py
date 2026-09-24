"""topic6 长任务运行器:飞书触发 → 方舟 MA Session → 事件桥接。

职责:

  - ``start_job``:接飞书触发词(热点报告/热点周报),创建 MA Session(挂 topic6-coordinator),
    落 ``pipeline_jobs``,启动异步 SSE 消费任务。
  - ``resume_job``:HITL 卡片回调后,把 HC 决策以 ``user.message`` 注入原 MA Session,
    再启一个 SSE 消费任务把 pipeline 从 wait_hc 拉回 running。
  - SSE 消费:``agent.tool_use`` / ``agent.message.delta`` 汇成一句进度文本发飞书;
    ``session.status_idle`` 时检查末尾消息是否是 HC 结构化 JSON,是则送 HITL 卡片,
    否则视为流程终态(成功/失败)落库并推最终链接。

不重写 SSE / SSE 解析,直接复用 :mod:`arkagent.ark` 里的 ``_open_event_stream`` 与
事件抽取工具。飞书发消息全部走 ``FeishuSender.send_to_chat``(同步),外面用
``run_in_executor`` 转异步。

同一 Session 同时只允许一个 SSE 消费任务:任务 ID 存在 ``_active_streams``,
resume 前先 cancel 前一个,避免重复订阅导致 429/事件重复。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from ..ark import ArkClient, event_error, event_progress, event_requires_action, event_text
from ..feishu import FeishuSender
from .pipeline_store import (
    HC_KINDS,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_RUNNING,
    STATUS_WAIT_HC,
    PipelineJob,
    PipelineStore,
)
from .topic6_progress_card import MAX_TOOL_LINES, build_progress_card

log = logging.getLogger("arkagent.topic6.runner")

# 触发词:精确匹配 prompt/00_角色与触发.md;命中即启动 topic6 pipeline。
TRIGGER_KEYWORDS = ("热点报告", "热点周报")

# 模式关键词(用户消息里带就用,不带默认 test)。
MODE_TEST_KEYWORDS = ("test", "小样本", "试跑")
MODE_FULL_KEYWORDS = ("full", "全量", "正式")

# 进度卡片 patch 节流:同一卡片至少间隔多少秒才发一次 patch,避免飞书频控。
PROGRESS_MIN_INTERVAL_SEC = 4.0


def parse_trigger(text: str) -> Optional[str]:
    """返回 mode(test|full),不是触发消息返回 None。"""
    if not text:
        return None
    lower = text.strip().lower()
    if not any(kw in text for kw in TRIGGER_KEYWORDS):
        return None
    if any(kw in lower for kw in MODE_FULL_KEYWORDS):
        return "full"
    return "test"


# ---- 事件解析 --------------------------------------------------------------


_HC_JSON_RE = re.compile(r"\{[^{}]*\"hc\"\s*:\s*\"(HC[123])\"[^{}]*\}", re.S)


def extract_hc_payload(text: str) -> Optional[dict]:
    """从 agent.message 正文里抠出 HC 结构化 JSON;抠不到返回 None。

    coordinator.system.md 已强制约定 HC1/HC2/HC3 在 end_turn 前必须落一个如下 JSON:

        {"hc": "HC1", "mode": "test", "project_dir": "...", ...}

    正文中允许穿插其它文本,但 JSON 块本身必须完整可解析。为了容错,先用正则找到
    第一个含 ``"hc": "HCx"`` 的花括号块,再交给 json.loads;失败返回 None,由上层
    视为普通完成事件。
    """
    if not text:
        return None
    match = _HC_JSON_RE.search(text)
    if not match:
        return None
    raw = match.group(0)
    try:
        value = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    hc_kind = value.get("hc")
    if hc_kind not in HC_KINDS:
        return None
    return value


# ---- Runner ----------------------------------------------------------------


@dataclass
class Topic6Config:
    """topic6 运行器所需的方舟资源常量(来自 ma-resources/skill_ids.json 汇总)。"""

    coordinator_agent_id: str
    environment_id: str
    memory_store_id: str = ""  # 挂到 /mnt/memory/;为空则不挂
    memory_mount_path: str = "/mnt/memory"
    vault_ids: tuple[str, ...] = ()
    # Session 首次消息发送后,SSE 消费的整体超时(秒)。给足 3.5h 主线 + 富余。
    session_timeout_sec: int = 6 * 3600


@dataclass
class _ProgressState:
    """一次 pipeline 的进度卡片本地状态(runner 内部使用)。

    - ``tool_lines``: 环形缓冲,存"HH:MM:SS · name · desc"字符串;超过 MAX_TOOL_LINES 自动挤旧。
    - ``overflow``: 已被环形缓冲挤掉的历史条数,渲染时用作"(更早 N 条已省略)"提示。
    - ``last_patch_at``: 单调时钟时间戳,用于节流。0 表示卡片刚发出、还没 patch 过。
    - ``started_at_wall``: 墙钟时间(秒),用于算 elapsed_sec。
    - ``last_signature``: 上一次渲染快照的哈希指纹,内容没变就不 patch。
    """

    started_at_wall: float = field(default_factory=time.time)
    tool_lines: deque = field(default_factory=lambda: deque(maxlen=MAX_TOOL_LINES))
    overflow: int = 0
    last_patch_at: float = 0.0
    last_signature: str = ""

    def push_tool_line(self, line: str) -> None:
        if len(self.tool_lines) == self.tool_lines.maxlen:
            self.overflow += 1
        self.tool_lines.append(line)


class Topic6Runner:
    """飞书 ↔ 方舟 MA topic6 pipeline 的桥接器。"""

    def __init__(
        self,
        ark: ArkClient,
        feishu: FeishuSender,
        store: PipelineStore,
        config: Topic6Config,
        loop: Optional[asyncio.AbstractEventLoop] = None,
        card_sender: Optional["Topic6CardSenderProtocol"] = None,
    ):
        self._ark = ark
        self._feishu = feishu
        self._store = store
        self._config = config
        self._loop = loop
        self._card_sender = card_sender  # 由 topic6_hitl 注入
        # ma_session_id → 正在消费的 SSE 任务;续跑前 cancel 前一个。
        self._active_streams: dict[str, asyncio.Task] = {}
        # job_id → 进度卡片本地状态(tool 环形缓冲 + 上次 patch 时间)。
        self._progress: dict[str, "_ProgressState"] = {}

    def bind_card_sender(self, sender: "Topic6CardSenderProtocol") -> None:
        self._card_sender = sender

    # ---- 入口 --------------------------------------------------------------

    async def start_job(
        self,
        *,
        chat_id: str,
        thread_id: str,
        user_open_id: str,
        mode: str,
        user_message: str,
    ) -> PipelineJob:
        """创建 MA Session 并启动 SSE 消费任务;返回落库后的 PipelineJob。

        - 会话隔离键:``(chat_id, thread_id, user_open_id)``。若已有活跃任务直接抛错,
          避免同一用户在同群同话题重复触发跑两遍。
        - Session 挂载 Memory Store(``/mnt/memory``)承载 topic6 的 lm/ 内容。
        - 首个 user.message 拼「触发词 + 模式 + 用户原始消息」,由 coordinator 解析并按
          system prompt 的 14 步执行。
        """
        existing = self._store.get_active_job_by_session_key(chat_id, thread_id, user_open_id)
        if existing:
            raise Topic6RunnerError(
                f"该会话已有活跃 pipeline(job_id={existing.job_id}, status={existing.status}),"
                "请等它跑完或先 /topic6 cancel。"
            )

        resources = []
        if self._config.memory_store_id:
            resources.append(
                {
                    "type": "memory_store",
                    "memory_store_id": self._config.memory_store_id,
                    "mount_path": self._config.memory_mount_path,
                }
            )
        session_id = await self._ark.create_session(
            self._config.coordinator_agent_id,
            self._config.environment_id,
            vault_ids=list(self._config.vault_ids),
            env_overrides={"FEISHU_USER_OPEN_ID": user_open_id},
            resources=resources,
        )
        # project_dir 由 coordinator 自己创建,这里先占位;pipeline 落库时统一按
        # /workspace/YYYYMMDD-mode-job_id 的规约(coordinator system prompt 里已定义)。
        project_dir = f"/workspace/topic6-{mode}-{session_id[:8]}"
        job = self._store.create_job(
            chat_id=chat_id,
            thread_id=thread_id,
            user_open_id=user_open_id,
            ma_session_id=session_id,
            mode=mode,
            project_dir=project_dir,
        )
        log.info(
            "topic6 start_job job=%s session=%s mode=%s chat=%s user=%s",
            job.job_id, session_id, mode, chat_id, user_open_id,
        )

        prompt = f"[topic6] 触发词=热点报告 mode={mode} project_dir={project_dir}\n用户原始消息:{user_message}"
        # 先发一张进度卡片当作"启动确认",拿到 message_id 落库,后续 patch 覆写这张卡即可,
        # 不再对每条 tool 事件单独 send_to_chat 刷屏。发失败也不阻塞主流程,只是这一次没有卡片。
        state = _ProgressState()
        self._progress[job.job_id] = state
        card = build_progress_card(
            job=job,
            status=STATUS_RUNNING,
            elapsed_sec=0,
            tool_lines=[],
            overflow=0,
        )
        try:
            message_id = await self._loop_run(
                lambda: self._feishu.send_interactive_card(chat_id, card)
            )
        except Exception as error:  # noqa: BLE001
            log.warning("topic6 progress card init failed job=%s: %s", job.job_id, error)
            message_id = None
        if message_id:
            self._store.set_progress_card_message_id(job.job_id, message_id)
            job.progress_card_message_id = message_id  # 内存态同步,后续路径不用再查库
            state.last_signature = self._signature(card)

        # SSE 消费任务在后台跑,不阻塞当前请求线程(飞书 3 秒响应窗口)。
        task = self._spawn_stream(job, first_user_message=prompt)
        self._active_streams[session_id] = task
        return job

    async def resume_job(self, job: PipelineJob, decision_message: str) -> None:
        """HITL 卡片回调路径:注入 user.message 后重新订阅 SSE。"""
        # cancel 前一个消费任务(通常已经因 idle 退出,但兜底)。
        prev = self._active_streams.get(job.ma_session_id)
        if prev is not None and not prev.done():
            prev.cancel()
        self._store.resume_running(job.job_id, next_phase=job.current_phase)
        # 复用同一张进度卡片(state 若已回收就重建,避免续跑丢卡)。
        if job.job_id not in self._progress:
            self._progress[job.job_id] = _ProgressState()
        task = self._spawn_stream(job, first_user_message=decision_message)
        self._active_streams[job.ma_session_id] = task

    # ---- SSE 消费 ----------------------------------------------------------

    def _spawn_stream(self, job: PipelineJob, first_user_message: str) -> asyncio.Task:
        loop = self._loop or asyncio.get_event_loop()
        return loop.create_task(self._drive_session(job, first_user_message))

    async def _drive_session(self, job: PipelineJob, first_user_message: str) -> None:
        session_id = job.ma_session_id
        job_id = job.job_id
        try:
            async with self._ark._open_event_stream(session_id) as stream:
                await self._ark.send_message(session_id, first_user_message)
                await self._consume_events(job_id, stream)
        except asyncio.CancelledError:
            log.info("topic6 stream cancelled job=%s session=%s", job_id, session_id)
            raise
        except Exception as error:  # noqa: BLE001 - 网关层兜底
            log.exception("topic6 stream failed job=%s: %s", job_id, error)
            reason = f"sse_stream_error: {error!s}"[:400]
            self._store.mark_failed(job_id, reason)
            await self._render_and_patch(
                job_id, status=STATUS_FAILED, error=reason, force=True
            )
        finally:
            # 消费退出即清引用,避免 dict 里挂着 done Task。
            if self._active_streams.get(session_id) and self._active_streams[session_id].done():
                self._active_streams.pop(session_id, None)

    async def _consume_events(self, job_id: str, stream) -> None:
        """SSE 事件循环:tool 事件 → 环形缓冲 + 节流 patch;terminal 走终态渲染。"""
        collected_messages: list[str] = []
        async for event in stream:
            etype = event.get("type") or ""

            # 阶段推进标记:coordinator 在切阶段时会调 `phase.mark` 之类的工具或直接发一句
            # `[phase] C1`,我们简单从 agent.message 里找 `[phase] X` 前缀更新 current_phase。
            phase_changed = False
            if etype == "agent.message":
                body = event_text(event)
                if body:
                    collected_messages.append(body)
                    phase_changed = self._maybe_update_phase(job_id, body)

            # 进度事件:塞进环形缓冲,由 patch 节流器决定何时刷卡片。
            progress = event_progress(event)
            if progress:
                self._append_progress_line(job_id, progress)

            if progress or phase_changed:
                # phase 切换是低频关键事件,强制立即刷,避免用户看到过时 phase。
                await self._render_and_patch(job_id, status=STATUS_RUNNING, force=phase_changed)

            # 失败终态。
            if etype in ("session.error", "session.status_failed"):
                error = event_error(event) or "session_failed"
                self._store.mark_failed(job_id, error[:400])
                await self._render_and_patch(
                    job_id, status=STATUS_FAILED, error=error[:400], force=True
                )
                return

            # 空闲终态:HC 或最终完成。
            if etype == "session.status_idle" and not event_requires_action(event):
                await self._handle_idle(job_id, collected_messages)
                return

    async def _handle_idle(self, job_id: str, collected: list[str]) -> None:
        """Session 空闲了:看最后一条 agent.message 是否落了 HC JSON。"""
        job = self._store.get_job(job_id)
        if not job:
            log.warning("topic6 handle_idle 找不到 job=%s", job_id)
            return
        last = collected[-1] if collected else ""
        payload = extract_hc_payload(last)
        if payload:
            hc_kind = str(payload["hc"])
            event_id = self._store.append_hc_event(job_id, hc_kind, payload)
            self._store.mark_wait_hc(job_id, hc_kind)
            # 进度卡片切到"等待审核"状态,提示用户到下方 HC 卡片操作。
            await self._render_and_patch(job_id, status=STATUS_WAIT_HC, force=True)
            if self._card_sender is None:
                # 兜底:没注入 HC 卡片发送器,退回一条纯文本让用户手工继续。
                await self._reply_async_by_job(
                    job_id,
                    f"⏸️ 等你审 {hc_kind}(卡片发送器未注入,回复 pass/reject/remark:xxx 继续)",
                )
                return
            try:
                message_id = await self._card_sender.send_hc_card(job, hc_kind, event_id, payload)
                # 卡片消息 id 写回 hc_event,便于回调时反查。
                if message_id:
                    self._store._conn.execute(  # noqa: SLF001 - runner 与 store 同包
                        "UPDATE pipeline_hc_events SET card_message_id = ? WHERE id = ?",
                        (message_id, event_id),
                    )
            except Exception as error:  # noqa: BLE001 - 卡片失败仍算 wait_hc,人工兜底
                log.exception("topic6 send hc card failed job=%s hc=%s: %s", job_id, hc_kind, error)
                await self._reply_async_by_job(
                    job_id, f"⚠️ {hc_kind} 卡片发送失败({error!s});可回复 pass/reject 手工继续"
                )
            return

        # 无 HC → 视为最终完成。拿最后一条消息里的链接当 online_url。
        online_url = _extract_first_url(last)
        self._store.mark_done(job_id, online_url=online_url)
        await self._render_and_patch(
            job_id, status=STATUS_DONE, online_url=online_url, force=True
        )
        # done 状态卡片已经带"打开报告"按钮,不再单独发文本。
        # 卡片本地状态在终态后可以释放,避免长期占用。
        self._progress.pop(job_id, None)

    # ---- helper --------------------------------------------------------------

    def _maybe_update_phase(self, job_id: str, body: str) -> bool:
        """扫 body 里的 `[phase] X` 前缀更新 current_phase;有变化返回 True。"""
        match = re.search(r"\[phase\]\s+([A-Z0-9]+)", body)
        if not match:
            return False
        new_phase = match.group(1)
        job = self._store.get_job(job_id)
        if job and job.current_phase == new_phase:
            return False
        self._store.update_phase(job_id, new_phase)
        return True

    def _append_progress_line(self, job_id: str, progress: str) -> None:
        """把一条 progress 事件塞进环形缓冲,带时间戳前缀。"""
        state = self._progress.get(job_id)
        if state is None:
            return
        ts = time.strftime("%H:%M:%S", time.localtime())
        state.push_tool_line(f"`{ts}` · {progress}")

    async def _render_and_patch(
        self,
        job_id: str,
        *,
        status: str,
        error: str = "",
        online_url: str = "",
        force: bool = False,
    ) -> None:
        """按当前 state 渲染卡片并 patch;节流由 last_patch_at 决定,force=True 时无视节流。

        没有 message_id(初次 send 失败)时跳过 — 用户看不到卡片但 SSE 消费不受影响。
        """
        state = self._progress.get(job_id)
        job = self._store.get_job(job_id)
        if state is None or job is None or not job.progress_card_message_id:
            return
        now = time.monotonic()
        if not force and now - state.last_patch_at < PROGRESS_MIN_INTERVAL_SEC:
            return
        elapsed_sec = int(time.time() - state.started_at_wall)
        card = build_progress_card(
            job=job,
            status=status,
            elapsed_sec=elapsed_sec,
            tool_lines=list(state.tool_lines),
            overflow=state.overflow,
            error=error,
            online_url=online_url,
        )
        signature = self._signature(card)
        if signature == state.last_signature and not force:
            # 内容没变(比如相邻两次 progress 完全同文),省一次 API 调用。
            return
        try:
            await self._loop_run(
                lambda: self._feishu.patch_interactive_card(
                    job.progress_card_message_id, card
                )
            )
        except Exception as error_patch:  # noqa: BLE001 - patch 失败不该拖垮 SSE
            log.warning(
                "topic6 progress card patch failed job=%s: %s", job_id, error_patch
            )
            return
        state.last_patch_at = now
        state.last_signature = signature

    @staticmethod
    def _signature(card: dict) -> str:
        return json.dumps(card, ensure_ascii=False, sort_keys=True)

    async def _loop_run(self, fn):
        """在 executor 里跑同步的 feishu SDK 调用,避免阻塞事件循环。"""
        loop = self._loop or asyncio.get_event_loop()
        return await loop.run_in_executor(None, fn)

    async def _reply_async_by_job(self, job_id: str, text: str) -> None:
        job = self._store.get_job(job_id)
        if job:
            await self._reply_async(job.chat_id, text)

    async def _reply_async(self, chat_id: str, text: str) -> None:
        loop = self._loop or asyncio.get_event_loop()
        try:
            await loop.run_in_executor(
                None, lambda: self._feishu.send_to_chat(chat_id, text)
            )
        except Exception as error:  # noqa: BLE001 - 回帖失败不该拖垮 SSE 消费
            log.warning("topic6 reply failed chat=%s: %s", chat_id, error)


class Topic6RunnerError(RuntimeError):
    """topic6 触发/续跑相关的业务错误。"""


class Topic6CardSenderProtocol:
    """topic6_hitl 会实现的卡片发送接口(避免 runner ↔ hitl 循环 import)。"""

    async def send_hc_card(
        self,
        _job: PipelineJob,
        _hc_kind: str,
        _event_id: int,
        _payload: dict,
    ) -> Optional[str]:
        """发送 HC 卡片,返回飞书 message_id(供反查)。"""
        raise NotImplementedError


_URL_RE = re.compile(r"https?://[^\s)】]+", re.I)


def _extract_first_url(text: str) -> str:
    if not text:
        return ""
    match = _URL_RE.search(text)
    return match.group(0) if match else ""
