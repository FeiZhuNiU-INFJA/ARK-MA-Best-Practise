"""topic6 长任务运行器:飞书触发 → 方舟 MA Session → 事件桥接。

职责:

  - ``start_job``:接飞书触发词(热点报告/热点周报),创建 MA Session(挂 topic6-coordinator),
    落 ``pipeline_jobs``,启动异步 SSE 消费任务。
  - ``resume_job``:HITL 卡片回调后,把 HC 决策以 ``user.message`` 注入原 MA Session,
    再启一个 SSE 消费任务把 pipeline 从 wait_hc 拉回 running。
  - SSE 消费:``agent.tool_use`` / ``agent.message.delta`` 汇成一句进度文本发飞书;
    ``session.status_idle`` 时检查末尾消息是否是 HC 结构化 JSON,是则把主卡切到 HITL,
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
import random
import re
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Callable, Optional
from urllib.parse import urlsplit

from arkagent.ark import ArkClient, event_error, event_progress, event_requires_action, event_text
from arkagent.feishu import FeishuSender
from arkagent.session_state import is_session_resumable, session_status_from_payload
try:
    from .pipeline_store import (
        FAILURE_SCOPE_SESSION,
        FAILURE_SCOPE_TASK,
        HC_KINDS,
        STATUS_DONE,
        STATUS_FAILED,
        STATUS_RUNNING,
        STATUS_STOPPING,
        STATUS_STOPPED,
        STATUS_WAIT_HC,
        PipelineJob,
        PipelineStore,
    )
    from .topic6_progress_card import MAX_TOOL_LINES, build_progress_card
except ImportError:  # pragma: no cover - standalone compatibility
    from pipeline_store import (
        FAILURE_SCOPE_SESSION,
        FAILURE_SCOPE_TASK,
        HC_KINDS,
        STATUS_DONE,
        STATUS_FAILED,
        STATUS_RUNNING,
        STATUS_STOPPING,
        STATUS_STOPPED,
        STATUS_WAIT_HC,
        PipelineJob,
        PipelineStore,
    )
    from topic6_progress_card import MAX_TOOL_LINES, build_progress_card

log = logging.getLogger("arkagent.topic6.runner")

# 触发词:精确匹配 prompt/00_角色与触发.md;命中即启动 topic6 pipeline。
TRIGGER_KEYWORDS = ("热点报告", "热点周报")

# 模式关键词(用户消息里带就用,不带默认 full)。
MODE_DEMO_KEYWORDS = ("demo", "演示")
MODE_FULL_KEYWORDS = ("full", "完整", "正式")
MODE_SKIP_SAMPLING_KEYWORDS = (
    "skip_sampling",
    "skip-sampling",
    "跳过采样",
    "跳过抽样",
)

# 进度卡片 patch 节流:同一卡片至少间隔多少秒才发一次 patch,避免飞书频控。
PROGRESS_MIN_INTERVAL_SEC = 4.0
# SSE 网络断线后的有限重连：1/2/4/8/16 秒，加抖动避免大量 Gateway 同时重连。
SSE_RECONNECT_MAX_ATTEMPTS = 5
SSE_RECONNECT_BASE_DELAY_SEC = 1.0
SSE_RECONNECT_MAX_DELAY_SEC = 16.0

# Topic6 业务项目只能落在该目录的直属子目录。Coordinator 负责按周期契约生成子目录名。
PROJECTS_ROOT = PurePosixPath("/workspace/Projects")


def normalize_user_text(text: str, mentioned_bot: bool = False) -> str:
    """群聊入站先去掉开头的 @机器人，避免机器人名称参与命令/触发词判断。"""
    normalized = text.strip()
    if mentioned_bot:
        normalized = re.sub(r"^(?:@\S+\s*)+", "", normalized).strip()
    return normalized


def parse_trigger(text: str) -> Optional[str]:
    """返回 mode(demo|full|skip_sampling),不是触发消息返回 None。"""
    if not text:
        return None
    lower = text.strip().lower()
    if not any(kw in text for kw in TRIGGER_KEYWORDS):
        return None
    if any(kw in lower for kw in MODE_SKIP_SAMPLING_KEYWORDS):
        return "skip_sampling"
    if any(kw in lower for kw in MODE_DEMO_KEYWORDS):
        return "demo"
    if any(kw in lower for kw in MODE_FULL_KEYWORDS):
        return "full"
    return "full"


# ---- 事件解析 --------------------------------------------------------------


def extract_hc_payload(text: str) -> Optional[dict]:
    """从 agent.message 正文里抠出 HC 结构化 JSON;抠不到返回 None。

    coordinator.system.md 已强制约定 HC1/HC2/HC3 在 end_turn 前必须落一个如下 JSON:

        {"hc": "HC1", "mode": "full", "project_dir": "...", ...}

    正文中允许穿插其它文本,但 JSON 块本身必须完整可解析。逐个尝试正文中的 ``{``
    起点并交给 ``JSONDecoder.raw_decode``，从而正确处理嵌套对象；找不到合法 HC
    对象时返回 None,由上层视为普通完成事件。
    """
    if not text:
        return None
    decoder = json.JSONDecoder()
    payload: Optional[dict] = None
    for start in (match.start() for match in re.finditer(r"\{", text)):
        try:
            value, _ = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("hc") in HC_KINDS:
            payload = value
            break
    return payload


def normalize_project_dir(value: object) -> Optional[str]:
    """校验 Topic6 项目绝对路径，只允许 ``/workspace/Projects`` 的直属子目录。"""
    raw = str(value or "").strip().strip("`")
    if not raw:
        return None
    candidate = PurePosixPath(raw)
    if candidate.parent != PROJECTS_ROOT or candidate.name in {"", ".", ".."}:
        return None
    return str(candidate)


def extract_project_dir(text: str) -> Optional[str]:
    """解析 Coordinator 的 ``[project_dir]`` 状态标记。"""
    if not text:
        return None
    match = re.search(r"(?m)^\[project_dir\]\s+([^\r\n]+?)\s*$", text)
    return normalize_project_dir(match.group(1)) if match else None


def validate_hc_payload(payload: dict) -> Optional[str]:
    """返回 HC payload 的契约错误；合法时返回 None。"""
    if payload.get("hc") == "HC3" and not str(payload.get("feishu_doc_url") or "").strip():
        return "HC3 缺少 feishu_doc_url：Phase F 飞书发布未完成"
    return None


# ---- Runner ----------------------------------------------------------------


@dataclass
class Topic6Config:
    """topic6 运行器所需的方舟资源常量(来自 ma-resources/skill_ids.json 汇总)。"""

    coordinator_agent_id: str
    environment_id: str
    memory_store_id: str = ""  # 会被平台自动挂到沙箱 /mnt/memory/;为空则不挂
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
        lease_validator: Optional[Callable[[str, str], bool]] = None,
        lease_activator: Optional[Callable[[str, str, str, str], bool]] = None,
    ):
        self._ark = ark
        self._feishu = feishu
        self._store = store
        self._config = config
        self._loop = loop
        self._card_sender = card_sender  # 由 topic6_hitl 注入
        self._lease_validator = lease_validator
        self._lease_activator = lease_activator
        # 串行化“检查活跃任务 → 创建 Session → 落 Job”，避免两个同时到达的触发
        # 都在落库前通过检查并各自创建一条长任务。
        self._start_lock = asyncio.Lock()
        # ma_session_id → 正在消费的 SSE 任务;续跑前 cancel 前一个。
        self._active_streams: dict[str, asyncio.Task] = {}
        # job_id → 进度卡片本地状态(tool 环形缓冲 + 上次 patch 时间)。
        self._progress: dict[str, "_ProgressState"] = {}
        # 同一任务的进度事件与 HITL 回调可能并发 patch；按 job 串行，避免旧快照覆盖新状态。
        self._card_locks: dict[str, asyncio.Lock] = {}

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
        user_vault_id: str = "",
    ) -> PipelineJob:
        """全局串行检查并启动任务；同一 Gateway 只允许一个活跃 Job。"""
        async with self._start_lock:
            return await self._start_job_locked(
                chat_id=chat_id,
                thread_id=thread_id,
                user_open_id=user_open_id,
                mode=mode,
                user_message=user_message,
                user_vault_id=user_vault_id,
            )

    async def start_routed_job(
        self,
        *,
        task_id: str,
        lease_token: str,
        chat_id: str,
        thread_id: str,
        user_open_id: str,
        mode: str,
        user_message: str,
        root_message_id: str,
        user_vault_id: str = "",
        existing_session_id: str = "",
        on_session_created: Optional[Callable[[str], None]] = None,
        on_job_created: Optional[Callable[[PipelineJob], None]] = None,
    ) -> PipelineJob:
        if mode not in ("demo", "full", "skip_sampling"):
            raise Topic6RunnerError(f"不支持的 Topic6 mode: {mode}")
        if self._lease_validator is None or not self._lease_validator(
            task_id, lease_token
        ):
            raise Topic6RunnerError("Topic6 lease token 无效或 task 不是当前 owner")
        async with self._start_lock:
            return await self._start_job_locked(
                chat_id=chat_id,
                thread_id=thread_id,
                user_open_id=user_open_id,
                mode=mode,
                user_message=user_message,
                user_vault_id=user_vault_id,
                task_id=task_id,
                root_message_id=root_message_id,
                existing_session_id=existing_session_id,
                skip_global_guard=True,
                lease_token=lease_token,
                on_session_created=on_session_created,
                on_job_created=on_job_created,
            )

    async def _start_job_locked(
        self,
        *,
        chat_id: str,
        thread_id: str,
        user_open_id: str,
        mode: str,
        user_message: str,
        user_vault_id: str = "",
        task_id: str = "",
        root_message_id: str = "",
        existing_session_id: str = "",
        skip_global_guard: bool = False,
        lease_token: str = "",
        on_session_created: Optional[Callable[[str], None]] = None,
        on_job_created: Optional[Callable[[PipelineJob], None]] = None,
    ) -> PipelineJob:
        """创建 MA Session、落库并启动 SSE 消费任务。

        - 整个 Gateway 任意 ``running`` / ``wait_hc`` Job 都会阻止新建任务。
        - Session 挂载 Memory Store(``/mnt/memory``)承载 topic6 的 lm/ 内容。
        - 首个 user.message 拼「触发词 + 模式 + 用户原始消息」,由 coordinator 解析并按
          system prompt 的 14 步执行。
        """
        if task_id:
            task_job = self._store.get_job_by_task(task_id)
            if task_job:
                return task_job
        existing = self._store.get_active_job()
        if existing and not skip_global_guard:
            state = "等待审核" if existing.status == STATUS_WAIT_HC else "运行中"
            raise Topic6RunnerError(
                f"当前已有热点周报任务{state}"
                f"（mode={existing.mode}，phase={existing.current_phase}）。"
                "请等待前一个任务结束后再发起。"
            )

        resources = []
        if self._config.memory_store_id:
            resources.append(
                {
                    "type": "memory_store",
                    "memory_store_id": self._config.memory_store_id,
                    "access": "read_only",
                }
            )
        env_overrides = {"FEISHU_USER_OPEN_ID": user_open_id}
        if task_id:
            env_overrides["TOPICX_TASK_ID"] = task_id
        if self._config.memory_store_id:
            # 方舟把每个 memstore 挂在 /mnt/memory/{memstore_id}/ 下(多 store 隔离),
            # 注入 ID 供 Agent bash 展开路径,例如 cat /mnt/memory/$TOPIC6_MEMORY_STORE_ID/topic6/_版本状态.md
            env_overrides["TOPIC6_MEMORY_STORE_ID"] = self._config.memory_store_id
        vault_ids = list(
            dict.fromkeys(
                vault_id
                for vault_id in (*self._config.vault_ids, user_vault_id)
                if vault_id
            )
        )
        create_kwargs = {
            "vault_ids": vault_ids,
            "env_overrides": env_overrides,
            "resources": resources,
        }
        if task_id:
            create_kwargs["title"] = f"topicx/{task_id}/topic6"
        session_id = existing_session_id or await self._ark.create_session(
            self._config.coordinator_agent_id,
            self._config.environment_id,
            **create_kwargs,
        )
        if on_session_created is not None:
            on_session_created(session_id)
        # 业务目录名依赖 Coordinator 解析出的周期。创建 Job 时保持未知，Coordinator
        # 创建目录后必须输出 [project_dir] 标记，SSE 消费器会立即回写真值。
        project_dir = ""
        job = self._store.create_job(
            chat_id=chat_id,
            thread_id=thread_id,
            user_open_id=user_open_id,
            ma_session_id=session_id,
            mode=mode,
            project_dir=project_dir,
            root_message_id=root_message_id,
            task_id=task_id,
        )
        if on_job_created is not None:
            on_job_created(job)
        if task_id and (
            self._lease_activator is None
            or not self._lease_activator(
                task_id, lease_token, session_id, job.job_id
            )
        ):
            raise Topic6RunnerError("Topic6 lease 激活失败")
        log.info(
            "topic6 start_job job=%s session=%s mode=%s chat=%s user=%s",
            job.job_id, session_id, mode, chat_id, user_open_id,
        )

        prompt = (
            f"[topic6] 触发词=热点报告 mode={mode} "
            f"workspace_root={PROJECTS_ROOT}\n用户原始消息:{user_message}"
        )
        # 先发一张进度卡片当作"启动确认",拿到 message_id 落库,后续 patch 覆写这张卡即可,
        # 不再对每条 tool 事件单独 send_to_chat 刷屏。发失败也不阻塞主流程,只是这一次没有卡片。
        state = _ProgressState()
        self._progress[job.job_id] = state
        self._card_locks[job.job_id] = asyncio.Lock()
        card = build_progress_card(
            job=job,
            status=STATUS_RUNNING,
            elapsed_sec=0,
            tool_lines=[],
            overflow=0,
        )
        try:
            if root_message_id:
                message_id, topic_thread_id = await self._loop_run(
                    lambda: self._feishu.send_interactive_card_in_thread(
                        root_message_id, card
                    )
                )
                if topic_thread_id:
                    self._store.set_topic_thread_id(job.job_id, topic_thread_id)
                    job.topic_thread_id = topic_thread_id
            else:
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
        # 审核回调刚把唯一主卡 patch 成“已通过/已备注”终态。必须在启动下一段 SSE 前
        # 立即恢复运行卡，否则下一段开头若只有 status/thinking 事件、迟迟没有可展示的
        # progress，卡片会一直停留在 HC 已处理状态，历史工具进度也看不到。
        await self._render_and_patch(job.job_id, status=STATUS_RUNNING, force=True)
        task = self._spawn_stream(job, first_user_message=decision_message)
        self._active_streams[job.ma_session_id] = task

    async def continue_job(
        self, chat_id: str, thread_id: str, user_open_id: str, message: str = "继续"
    ) -> Optional[PipelineJob]:
        """/continue 续跑:把一条 user.message 注入最近一个可续跑的 Session,重订 SSE。

        - 无可续跑 job → 返回 None(调用方给“没有可续跑任务”的提示)。
        - 命中(running/wait_hc/failed/stopped)→ 复用 resume_job 注入 message 并重新
          订阅 SSE,返回该 job。

        主场景:卡片已显示“执行失败，可重试”,但 MA Session 仍是 idle/running。
        真正 failed/terminated 的 Session 不允许继续。
        """
        job = self._store.get_resumable_job_by_session_key(
            chat_id, thread_id, user_open_id
        )
        if job is None:
            return None
        if job.failure_scope not in ("", "recoverable"):
            raise Topic6RunnerError("当前任务已终止，不能继续")
        session = await self._ark.get_session(job.ma_session_id)
        session_status = session_status_from_payload(session)
        if not is_session_resumable(session_status):
            self._store.mark_failed(
                job.job_id,
                f"session_not_resumable:{session_status or 'unknown'}",
                failure_scope=FAILURE_SCOPE_SESSION,
                retry_strategy="",
            )
            raise Topic6RunnerError(
                f"业务 Session 状态为 {session_status or 'unknown'}，不能继续当前任务"
            )
        log.info(
            "topic6 /continue resume job=%s session=%s session_status=%s "
            "prev_status=%s phase=%s",
            job.job_id,
            job.ma_session_id,
            session_status,
            job.status,
            job.current_phase,
        )
        await self.resume_job(job, message)
        return job

    async def cancel_active_job(
        self, chat_id: str, thread_id: str, user_open_id: str
    ) -> Optional[PipelineJob]:
        """取消该会话键上的活跃 job(SSE task + status → failed),让 /new 可以马上开新任务。

        - 无活跃 job → 返回 None(调用方给"当前没有可取消的任务"的提示)。
        - 有活跃 job → cancel SSE task、mark_failed(reason=cancelled_by_user)、
          刷一次进度卡片到失败终态,返回该 job。
        """
        job = self._store.get_active_job_by_session_key(chat_id, thread_id, user_open_id)
        if job is None:
            return None

        task = self._active_streams.pop(job.ma_session_id, None)
        if task is not None and not task.done():
            task.cancel()

        reason = "cancelled_by_user"
        self._store.mark_failed(
            job.job_id, reason, failure_scope=FAILURE_SCOPE_TASK
        )
        await self._render_and_patch(
            job.job_id, status=STATUS_FAILED, error=reason, force=True
        )
        return job

    async def recover_active_jobs(self) -> dict[str, str]:
        """Gateway 重启后对账所有活跃任务，并恢复必要的 SSE 订阅。

        恢复过程只读取 MA 已持久化事件，绝不发送 ``user.message``。因此它不会替
        用户执行 ``/continue``，只会把本地 SQLite 状态追到 MA 的最后可见状态。
        """
        results: dict[str, str] = {}
        for job in self._store.list_active_jobs():
            try:
                results[job.job_id] = await self.recover_job(job)
            except Exception as error:  # noqa: BLE001 - 单个 Session 失败不阻塞其它恢复
                log.exception(
                    "topic6 startup recovery failed job=%s session=%s: %s",
                    job.job_id,
                    job.ma_session_id,
                    error,
                )
                results[job.job_id] = "recovery_error"
        return results

    async def recover_job(self, job: PipelineJob) -> str:
        """根据历史事件恢复一个本地活跃任务，返回已采取的恢复动作。"""
        events = await self._ark.list_session_events(job.ma_session_id)
        if job.status == STATUS_STOPPING:
            return "stopping"
        messages = [
            body
            for event in events
            if event.get("type") == "agent.message"
            for body in [event_text(event)]
            if body
        ]
        for body in messages:
            self._maybe_update_project_dir(job.job_id, body)
            self._maybe_update_phase(job.job_id, body)

        terminal_types = {
            "user.interrupt",
            "session.error",
            "session.status_failed",
            "session.status_terminated",
            "session.status_idle",
        }
        last_terminal_index = max(
            (
                index
                for index, event in enumerate(events)
                if event.get("type") in terminal_types
            ),
            default=-1,
        )
        # 某个旧 idle/error 之后如果又有事件，说明用户或审核回调已让 Session
        # 进入下一轮；历史尾部尚无终态时仅恢复订阅，不能把旧 idle 当当前状态。
        if last_terminal_index == -1 or last_terminal_index < len(events) - 1:
            self._restore_stream(job)
            return "subscribed"

        terminal = events[last_terminal_index]
        terminal_type = terminal.get("type") or ""
        if terminal_type == "user.interrupt":
            reason = "用户在方舟后台手动停止了 Session"
            self._store.mark_stopped(job.job_id, reason)
            await self._render_and_patch(
                job.job_id, status=STATUS_STOPPED, error=reason, force=True
            )
            return "stopped"
        if terminal_type in {
            "session.error",
            "session.status_failed",
            "session.status_terminated",
        }:
            error = event_error(terminal) or (
                "session_terminated"
                if terminal_type == "session.status_terminated"
                else "session_failed"
            )
            self._store.mark_failed(
                job.job_id,
                error[:400],
                failure_scope=FAILURE_SCOPE_SESSION,
            )
            await self._render_and_patch(
                job.job_id, status=STATUS_FAILED, error=error[:400], force=True
            )
            return "failed"

        # idle：用完整消息历史重建 HC 或 Phase H 的最终结果。recovering=True
        # 会复用已有的未决 HC 事件，避免 Gateway 重启时重复生成审核卡。
        await self._handle_idle(job.job_id, messages, recovering=True)
        current = self._store.get_job(job.job_id)
        return current.status if current is not None else "missing"

    def _restore_stream(self, job: PipelineJob) -> None:
        """为仍未见终态的 Session 恢复 SSE，不注入任何新消息。"""
        existing = self._active_streams.get(job.ma_session_id)
        if existing is not None and not existing.done():
            return
        self._progress.setdefault(
            job.job_id, _ProgressState(started_at_wall=float(job.started_at))
        )
        self._card_locks.setdefault(job.job_id, asyncio.Lock())
        task = self._spawn_stream(job, first_user_message=None)
        self._active_streams[job.ma_session_id] = task
        log.info(
            "topic6 restored SSE subscription job=%s session=%s",
            job.job_id,
            job.ma_session_id,
        )

    # ---- SSE 消费 ----------------------------------------------------------

    def _spawn_stream(
        self, job: PipelineJob, first_user_message: Optional[str]
    ) -> asyncio.Task:
        loop = self._loop or asyncio.get_event_loop()
        return loop.create_task(self._drive_session(job, first_user_message))

    async def _drive_session(
        self, job: PipelineJob, first_user_message: Optional[str]
    ) -> None:
        """消费 MA 事件流；瞬时网络断线时重连但不重复发送 user.message。"""
        session_id = job.ma_session_id
        job_id = job.job_id
        sent_initial_message = first_user_message is None
        seen_event_ids: set[str] = set()
        try:
            for attempt in range(SSE_RECONNECT_MAX_ATTEMPTS + 1):
                try:
                    async with self._ark._open_event_stream(session_id) as stream:
                        if not sent_initial_message:
                            await self._ark.send_message(session_id, first_user_message)
                            sent_initial_message = True
                        terminal = await self._consume_events(
                            job_id, stream, seen_event_ids=seen_event_ids
                        )
                        if terminal:
                            return
                        raise RuntimeError("方舟事件流意外结束，未观察到 Session 终态")
                except asyncio.CancelledError:
                    raise
                except Exception:
                    if attempt >= SSE_RECONNECT_MAX_ATTEMPTS:
                        raise
                    delay = min(
                        SSE_RECONNECT_BASE_DELAY_SEC * (2**attempt),
                        SSE_RECONNECT_MAX_DELAY_SEC,
                    )
                    # 20% 抖动，既避免同步重连，也让最长等待受上限约束。
                    delay *= random.uniform(0.8, 1.2)
                    log.warning(
                        "topic6 SSE disconnected; reconnecting job=%s session=%s "
                        "attempt=%s/%s delay=%.1fs",
                        job_id,
                        session_id,
                        attempt + 1,
                        SSE_RECONNECT_MAX_ATTEMPTS,
                        delay,
                    )
                    await asyncio.sleep(delay)
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
            if self._active_streams.get(session_id) is asyncio.current_task():
                self._active_streams.pop(session_id, None)

    async def _consume_events(
        self,
        job_id: str,
        stream,
        *,
        seen_event_ids: Optional[set[str]] = None,
    ) -> bool:
        """SSE 事件循环:tool 事件 → 环形缓冲 + 节流 patch;terminal 走终态渲染。"""
        collected_messages: list[str] = []
        async for event in stream:
            event_id = event.get("id")
            if isinstance(event_id, str) and event_id:
                if seen_event_ids is not None:
                    if event_id in seen_event_ids:
                        continue
                    seen_event_ids.add(event_id)
            etype = event.get("type") or ""

            # 阶段推进标记:coordinator 在切阶段时会调 `phase.mark` 之类的工具或直接发一句
            # `[phase] C1`,我们简单从 agent.message 里找 `[phase] X` 前缀更新 current_phase。
            phase_changed = False
            if etype == "agent.message":
                body = event_text(event)
                if body:
                    collected_messages.append(body)
                    self._maybe_update_project_dir(job_id, body)
                    phase_changed = self._maybe_update_phase(job_id, body)

            # 进度事件:塞进环形缓冲,由 patch 节流器决定何时刷卡片。
            progress = event_progress(event)
            if progress:
                self._append_progress_line(job_id, progress)

            if progress or phase_changed:
                # phase 切换是低频关键事件,强制立即刷,避免用户看到过时 phase。
                await self._render_and_patch(job_id, status=STATUS_RUNNING, force=phase_changed)

            if etype == "user.interrupt":
                current = self._store.get_job(job_id)
                if current and current.status == STATUS_STOPPING:
                    continue
                reason = "用户在方舟后台手动停止了 Session"
                self._store.mark_stopped(job_id, reason)
                await self._render_and_patch(
                    job_id, status=STATUS_STOPPED, error=reason, force=True
                )
                return True

            # 失败终态。
            if etype in (
                "session.error",
                "session.status_failed",
                "session.status_terminated",
            ):
                error = event_error(event) or (
                    "session_terminated"
                    if etype == "session.status_terminated"
                    else "session_failed"
                )
                # 这条终态分支过去是静默的(只改库+刷卡),导致日志里无法区分
                # “真·Session 终态(failed/terminated)”和其他本地收口的假失败
                # (SSE 断线/契约错/reject)。补一行 error 日志作为真失败实锤。
                log.error(
                    "topic6 session terminal job=%s etype=%s error=%s",
                    job_id, etype, error[:400],
                )
                self._store.mark_failed(
                    job_id,
                    error[:400],
                    failure_scope=FAILURE_SCOPE_SESSION,
                )
                await self._render_and_patch(
                    job_id, status=STATUS_FAILED, error=error[:400], force=True
                )
                return True

            # 空闲终态:HC 或最终完成。
            if etype == "session.status_idle" and not event_requires_action(event):
                current = self._store.get_job(job_id)
                if current and current.status == STATUS_STOPPING:
                    return True
                await self._handle_idle(job_id, collected_messages)
                return True
        return False

    async def _handle_idle(
        self, job_id: str, collected: list[str], *, recovering: bool = False
    ) -> None:
        """Session 空闲了:看最后一条 agent.message 是否落了 HC JSON。"""
        job = self._store.get_job(job_id)
        if not job:
            log.warning("topic6 handle_idle 找不到 job=%s", job_id)
            return
        last = collected[-1] if collected else ""
        payload = extract_hc_payload(last)
        if payload:
            hc_kind = str(payload["hc"])
            payload_project_dir = normalize_project_dir(payload.get("project_dir"))
            if payload_project_dir:
                self._maybe_update_project_dir(
                    job_id, f"[project_dir] {payload_project_dir}"
                )
                job = self._store.get_job(job_id) or job
            payload_error = validate_hc_payload(payload)
            if payload_error:
                log.error(
                    "topic6 invalid HC payload job=%s hc=%s: %s",
                    job_id,
                    hc_kind,
                    payload_error,
                )
                self._store.mark_failed(job_id, payload_error)
                await self._render_and_patch(
                    job_id,
                    status=STATUS_FAILED,
                    error=payload_error,
                    force=True,
                )
                await self._reply_async_by_job(job_id, f"❌ {payload_error}")
                return
            existing_hc = self._store.latest_hc_for_job(job_id, hc_kind)
            if recovering and existing_hc and existing_hc.resolved_at is None:
                # Gateway 断线后本地已记下 HC，但卡片 patch 尚未送达或旧卡不可见。
                # 重建同一条审核事件，不能 append，否则同一个按钮会生成两段决策审计。
                self._store.mark_wait_hc(job_id, hc_kind)
                job = self._store.get_job(job_id) or job
                if self._card_sender is not None:
                    message_id = await self._card_sender.send_hc_card(
                        job, hc_kind, existing_hc.id, existing_hc.payload
                    )
                    if message_id and message_id != existing_hc.card_message_id:
                        self._store._conn.execute(  # noqa: SLF001 - runner 与 store 同包
                            "UPDATE pipeline_hc_events SET card_message_id = ? WHERE id = ?",
                            (message_id, existing_hc.id),
                        )
                else:
                    await self._render_and_patch(
                        job_id, status=STATUS_WAIT_HC, force=True
                    )
                log.info(
                    "topic6 restored pending hc card job=%s hc=%s event=%s",
                    job_id,
                    hc_kind,
                    existing_hc.id,
                )
                return
            event_id = self._store.append_hc_event(job_id, hc_kind, payload)
            self._store.mark_wait_hc(job_id, hc_kind)
            job = self._store.get_job(job_id) or job
            if self._card_sender is None:
                # 兜底:没注入 HC 卡片发送器,退回一条纯文本让用户手工继续。
                await self._render_and_patch(job_id, status=STATUS_WAIT_HC, force=True)
                await self._reply_async_by_job(
                    job_id,
                    f"⏸️ 等你审 {hc_kind}(卡片发送器未注入,发送 /continue 注入「继续」推进该阶段)",
                )
                return
            try:
                message_id = await self._card_sender.send_hc_card(job, hc_kind, event_id, payload)
                # HC 与运行进度复用同一个 message_id；写回事件便于回调审计与兜底反查。
                if message_id:
                    self._store._conn.execute(  # noqa: SLF001 - runner 与 store 同包
                        "UPDATE pipeline_hc_events SET card_message_id = ? WHERE id = ?",
                        (message_id, event_id),
                    )
            except Exception as error:  # noqa: BLE001 - 卡片失败仍算 wait_hc,人工兜底
                log.exception("topic6 send hc card failed job=%s hc=%s: %s", job_id, hc_kind, error)
                await self._render_and_patch(job_id, status=STATUS_WAIT_HC, force=True)
                await self._reply_async_by_job(
                    job_id, f"⚠️ {hc_kind} 卡片发送失败({error!s});可回复 pass/reject 手工继续"
                )
            return

        # 只有 Phase H 的结构化结果和妙搭域名同时有效才算完成。不能从自由文本里
        # 抓第一个 URL，否则飞书文档链接或错误信息中的排障链接会被误当成发布结果。
        online_url = extract_final_online_url(last)
        if not online_url:
            reason = "Session 已结束，但 Phase H 未返回有效的妙搭 online_url"
            self._store.mark_failed(job_id, reason)
            await self._render_and_patch(
                job_id, status=STATUS_FAILED, error=reason, force=True
            )
            return
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

    def _maybe_update_project_dir(self, job_id: str, body: str) -> bool:
        project_dir = extract_project_dir(body)
        if not project_dir:
            return False
        job = self._store.get_job(job_id)
        if not job or job.project_dir == project_dir:
            return False
        if job.project_dir:
            log.warning(
                "topic6 project_dir changed job=%s old=%s new=%s",
                job_id,
                job.project_dir,
                project_dir,
            )
        self._store.update_project_dir(job_id, project_dir)
        log.info("topic6 project_dir resolved job=%s path=%s", job_id, project_dir)
        return True

    def _append_progress_line(self, job_id: str, progress: str) -> None:
        """把一条 progress 事件塞进环形缓冲,带时间戳前缀。"""
        state = self._progress.get(job_id)
        if state is None:
            return
        ts = time.strftime("%H:%M:%S", time.localtime())
        state.push_tool_line(f"{ts} · {progress}")

    def _review_lines(self, job_id: str) -> list[str]:
        """格式化已落库的 HC 审核轨迹，供后续运行态和终态持续展示。"""
        decision_meta = {
            "pass": ("✅", "通过"),
            "reject": ("❌", "打回"),
            "remark": ("📝", "备注通过"),
        }
        lines: list[str] = []
        for event in self._store.list_resolved_hc_for_job(job_id):
            emoji, label = decision_meta.get(
                event.user_decision, ("•", event.user_decision or "已处理")
            )
            operator = event.operator_label or "审核人未记录"
            resolved_at = time.strftime(
                "%H:%M",
                time.localtime(event.resolved_at or event.created_at),
            )
            lines.append(
                f"{event.hc_kind} · {emoji} {label} · {operator} · {resolved_at}"
            )
        return lines

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
        if job.status in (STATUS_STOPPING, STATUS_STOPPED):
            status = job.status
            error = job.last_error or error
        now = time.monotonic()
        if not force and now - state.last_patch_at < PROGRESS_MIN_INTERVAL_SEC:
            return
        elapsed_sec = int(time.time() - state.started_at_wall)
        card = build_progress_card(
            job=job,
            status=status,
            elapsed_sec=elapsed_sec,
            tool_lines=list(state.tool_lines),
            review_lines=self._review_lines(job_id),
            overflow=state.overflow,
            error=error,
            online_url=online_url,
        )
        signature = self._signature(card)
        if signature == state.last_signature and not force:
            # 内容没变(比如相邻两次 progress 完全同文),省一次 API 调用。
            return
        try:
            await self.patch_job_card(job_id, card)
        except Exception as error_patch:  # noqa: BLE001 - patch 失败不该拖垮 SSE
            log.warning(
                "topic6 progress card patch failed job=%s: %s", job_id, error_patch
            )
            return
        state.last_patch_at = now
        state.last_signature = signature

    async def patch_job_card(self, job_id: str, card: dict) -> Optional[str]:
        """串行 patch 当前任务唯一的交互卡片；没有 message_id 时返回 None。"""
        job = self._store.get_job(job_id)
        if job is None or not job.progress_card_message_id:
            return None
        lock = self._card_locks.setdefault(job_id, asyncio.Lock())
        async with lock:
            await self._loop_run(
                lambda: self._feishu.patch_interactive_card(
                    job.progress_card_message_id, card
                )
            )
        state = self._progress.get(job_id)
        if state is not None:
            state.last_signature = self._signature(card)
            state.last_patch_at = time.monotonic()
        return job.progress_card_message_id

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
            await self._reply_async(
                job.chat_id, text, root_message_id=job.root_message_id
            )

    async def _reply_async(
        self, chat_id: str, text: str, root_message_id: str = ""
    ) -> None:
        loop = self._loop or asyncio.get_event_loop()
        try:
            if root_message_id:
                await loop.run_in_executor(
                    None,
                    lambda: self._feishu.reply_in_thread(root_message_id, text),
                )
            else:
                await loop.run_in_executor(
                    None, lambda: self._feishu.send_to_chat(chat_id, text)
                )
        except Exception as error:  # noqa: BLE001 - 回帖失败不该拖垮 SSE 消费
            log.warning("topic6 reply failed chat=%s: %s", chat_id, error)


class Topic6RunnerError(RuntimeError):
    """topic6 触发/续跑相关的业务错误。"""


class Topic6CardSenderProtocol:
    """topic6_hitl 会实现的审核态切换接口(避免 runner ↔ hitl 循环 import)。"""

    async def send_hc_card(
        self,
        _job: PipelineJob,
        _hc_kind: str,
        _event_id: int,
        _payload: dict,
    ) -> Optional[str]:
        """把任务主卡切到 HC 状态,返回复用的飞书 message_id(供反查)。"""
        raise NotImplementedError


def extract_final_online_url(text: str) -> str:
    """读取 Phase H 结构化结果中的妙搭线上 URL。

    自由文本中的 URL 不可信：报告正文通常先出现飞书 docx 链接，而且 Markdown /
    JSON 标点会被宽松正则一并吞入。这里只接受显式 ``online_url`` 字段，并校验
    HTTPS 与妙搭发布域名。
    """
    if not text:
        return ""
    decoder = json.JSONDecoder()
    for start in (match.start() for match in re.finditer(r"\{", text)):
        try:
            value, _ = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict):
            continue
        candidate = str(value.get("online_url") or "").strip()
        if not candidate:
            continue
        parsed = urlsplit(candidate)
        hostname = (parsed.hostname or "").lower()
        is_aiforce = hostname == "aiforce.cloud" or hostname.endswith(".aiforce.cloud")
        is_feishuapp = (
            (hostname == "feishuapp.cn" or hostname.endswith(".feishuapp.cn"))
            and re.fullmatch(r"/app/app_[A-Za-z0-9]+/?", parsed.path) is not None
        )
        if (
            parsed.scheme == "https"
            and parsed.query == ""
            and parsed.fragment == ""
            and parsed.username is None
            and parsed.password is None
            and (is_aiforce or is_feishuapp)
        ):
            return candidate
    return ""
