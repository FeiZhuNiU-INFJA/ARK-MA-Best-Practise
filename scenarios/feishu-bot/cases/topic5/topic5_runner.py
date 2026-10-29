"""topic5 长任务运行器:飞书触发 → 方舟 MA Session(薄 Coordinator)→ 事件桥接。

⚠️ host-only:本模块 import ``arkagent.ark`` / ``arkagent.feishu``,只能在装了 arkagent
框架的 Gateway 宿主进程里 import/运行,**无法在 MA 云沙箱内独立 import 测试**。
纯 stdlib 的 pipeline_store / topic5_progress_card / config 才可沙箱测试。

职责(对齐 plan 波次6 line127-134 的 HC 边界):

  - ``start_job``:接飞书触发词(复盘报告/营销复盘),创建 MA Session(挂 topic5-coordinator),
    落 ``pipeline_jobs``,启动异步 SSE 消费任务。薄 Coordinator 在沙箱里跑
    ``python run_pipeline.py start --dag ... --task ... --run ...`` 驱动确定性引擎。
  - ``resume_job``:HITL 卡片回调后,把 HC 决策以 ``user.message``(``HCx pass/reject/remark: note``)
    注入原 MA Session,薄 Coordinator 据此跑 ``run_pipeline.py resume --hc {kind} --decision ...``,
    把 pipeline 从 wait_hc 拉回 running。
  - SSE 消费:``agent.tool_use`` / ``agent.message.delta`` 汇成进度文本发飞书;
    ``session.status_idle`` 时检查末尾消息是否带 HC 结构化 JSON(引擎 HumanPause → hc_pending.json
    → 薄 Agent 输出),是则把主卡切到 HITL,否则视为流程终态(成功出飞书报告/失败)。

同一 Session 同时只允许一个 SSE 消费任务,resume 前先 cancel 前一个,避免重复订阅。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlsplit

from arkagent.ark import ArkClient, event_error, event_progress, event_requires_action, event_text
from arkagent.feishu import FeishuSender, ResourceRef
from arkagent.session_state import is_session_resumable, session_status_from_payload
try:
    from .pipeline_store import (
        FAILURE_SCOPE_SESSION,
        FAILURE_SCOPE_TASK,
        HC_KINDS,
        RETRY_STRATEGY_NODE,
        STATUS_DONE,
        STATUS_FAILED,
        STATUS_PAUSED,
        STATUS_RUNNING,
        STATUS_STOPPING,
        STATUS_STOPPED,
        STATUS_WAIT_HC,
        STATUS_WAIT_INPUT,
        PipelineJob,
        PipelineStore,
    )
    from .topic5_intake import IntakeValidationError, validate_xlsx_container
    from .topic5_progress_card import (
        MAX_TOOL_LINES,
        build_progress_card,
        format_review_lines,
    )
except ImportError:  # pragma: no cover - standalone compatibility
    from pipeline_store import (
        FAILURE_SCOPE_SESSION,
        FAILURE_SCOPE_TASK,
        HC_KINDS,
        RETRY_STRATEGY_NODE,
        STATUS_DONE,
        STATUS_FAILED,
        STATUS_PAUSED,
        STATUS_RUNNING,
        STATUS_STOPPING,
        STATUS_STOPPED,
        STATUS_WAIT_HC,
        STATUS_WAIT_INPUT,
        PipelineJob,
        PipelineStore,
    )
    from topic5_intake import IntakeValidationError, validate_xlsx_container
    from topic5_progress_card import (
        MAX_TOOL_LINES,
        build_progress_card,
        format_review_lines,
    )

log = logging.getLogger("arkagent.topic5.runner")

# 触发词:精确匹配 topic5 场景角色;命中即启动复盘报告 pipeline。
TRIGGER_KEYWORDS = ("复盘报告", "营销复盘", "活动复盘")

# 模式关键词(用户消息里带就用,不带默认 full)。demo 在 pipeline 清洗后最多取 500 条。
MODE_DEMO_KEYWORDS = ("demo", "演示", "小样本", "试跑")
MODE_FULL_KEYWORDS = ("full", "完整", "正式", "全量")

# 进度卡片 patch 节流:同一卡片至少间隔多少秒才发一次 patch,避免飞书频控。
PROGRESS_MIN_INTERVAL_SEC = 4.0
# SSE 网络断线后的有限重连:1/2/4/8/16 秒,加抖动避免大量 Gateway 同时重连。
SSE_RECONNECT_MAX_ATTEMPTS = 5
SSE_RECONNECT_BASE_DELAY_SEC = 1.0
SSE_RECONNECT_MAX_DELAY_SEC = 16.0
MAX_ACTIVITY_XLSX_BYTES = 50 * 1024 * 1024
RECOVERY_JOB_TIMEOUT_SEC = 30.0
_G3_REQUIRED_INPUT_KEYS = {
    "report_md",
    "requirement_confirmed",
    "stats_bundle",
    "chart_urls",
    "validator_rules",
}


def _event_contract_text(event: dict) -> str:
    """Return exact durable-pipeline envelopes from tool stdout."""
    if event.get("type") != "agent.tool_result":
        return ""
    body = event_text(event)
    if (
        "HC_PENDING " in body
        or "FINAL_RESULT " in body
        or re.search(r"\bstatus\s*[:=]\s*failed\b", body, re.IGNORECASE)
    ):
        return body
    return ""


def _latest_turn_events(events: list[dict]) -> list[dict]:
    """Keep only events after the most recent user message."""
    last_user_index = max(
        (
            index
            for index, event in enumerate(events)
            if event.get("type") == "user.message"
        ),
        default=-1,
    )
    return events[last_user_index + 1:]


def normalize_user_text(text: str, mentioned_bot: bool = False) -> str:
    """群聊入站先去掉开头的 @机器人,避免机器人名称参与命令/触发词判断。"""
    normalized = text.strip()
    if mentioned_bot:
        normalized = re.sub(r"^(?:@\S+\s*)+", "", normalized).strip()
    return normalized


def parse_trigger(text: str) -> Optional[str]:
    """返回 mode(demo|full),不是触发消息返回 None。"""
    if not text:
        return None
    lower = text.strip().lower()
    if not any(kw in text for kw in TRIGGER_KEYWORDS):
        return None
    if any(kw in lower for kw in MODE_DEMO_KEYWORDS):
        return "demo"
    if any(kw in lower for kw in MODE_FULL_KEYWORDS):
        return "full"
    return "full"


# ---- 事件解析 --------------------------------------------------------------


# 兜底"文本意图检测":Agent 违约(说要输出 HC JSON 但没真输出)时,文本出现 HC 关键词
# + 明确"等确认"意图,就伪造占位 payload,免得 pipeline 被吞。
_HC_INTENT_KEYWORDS = (
    "等用户",
    "等你审",
    "请审核",
    "请点击卡片",
    "等待用户",
    "等待确认",
    "点击卡片确认",
    "确认后继续",
    "人工关卡",
    "输出 HC",
)


def extract_hc_payload(text: str) -> Optional[dict]:
    """从 agent.message 正文里抠出 HC 结构化 JSON;抠不到返回 None。

    薄 Coordinator 在引擎 HumanPause 后读 ``hc_pending.json``,在 end_turn 前落一个:

        {"hc": "G3", "node": "n17_validate", "prompt": "...", "expect": [...]}

    正文允许穿插其它文本,JSON 块本身必须完整可解析。``hc`` 必须命中 HC_KINDS。
    """
    if not text:
        return None
    decoder = json.JSONDecoder()
    payload: Optional[dict] = None
    for start in (match.start() for match in re.finditer(r"\{", text)):
        try:
            value, _ = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            value = _repair_truncated_json_object(text[start:])
        if isinstance(value, dict):
            hc_kind = value.get("hc") or value.get("hc_kind")
            if hc_kind not in HC_KINDS:
                continue
            payload = dict(value)
            payload["hc"] = hc_kind
            break
    return payload


def _hc_payload_score(payload: dict) -> tuple[int, int]:
    """Prefer complete gate payloads when one turn emits multiple snapshots."""
    context = payload.get("review_context")
    score = 0 if payload.get("__fallback__") else 10
    if isinstance(context, dict):
        score += 10
        hc_kind = payload.get("hc")
        if hc_kind == "G2":
            score += sum(
                10
                for key in ("analysis_plan", "execution_plan", "selected_modules")
                if isinstance(context.get(key), dict)
            )
        elif hc_kind == "N10":
            score += sum(
                10
                for key in ("opinion_candidates", "ugc_category_list")
                if isinstance(context.get(key), dict)
            )
        elif hc_kind == "G3" and isinstance(context.get("qc_report"), dict):
            score += 50
        elif hc_kind == "G4":
            draft = context.get("delivery_draft")
            if isinstance(draft, dict) and draft.get("revision_id"):
                score += 50
    return score, len(json.dumps(payload, ensure_ascii=False))


def _best_hc_payload(collected: list[str]) -> Optional[dict]:
    candidates = [
        payload
        for text in collected
        for payload in [extract_hc_payload(text)]
        if payload is not None
    ]
    if not candidates:
        return None
    # A single Agent turn may internally pass G3 and continue to G4 before it
    # becomes idle. Never let a richer historical G3 payload outrank the later
    # gate; only compare snapshots belonging to the last emitted HC kind.
    latest_hc = candidates[-1]["hc"]
    return max(
        (payload for payload in candidates if payload["hc"] == latest_hc),
        key=_hc_payload_score,
    )


def _repair_truncated_json_object(candidate: str) -> Optional[dict]:
    """Repair only an otherwise complete JSON object missing trailing closers."""
    value = candidate.strip()
    stack: list[str] = []
    in_string = False
    escaped = False
    pairs = {"}": "{", "]": "["}
    for char in value:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append(char)
        elif char in "}]":
            if not stack or stack.pop() != pairs[char]:
                return None
    if in_string or not stack or len(stack) > 3:
        return None
    suffix = "".join("}" if opener == "{" else "]" for opener in reversed(stack))
    try:
        repaired = json.loads(value + suffix)
    except json.JSONDecodeError:
        return None
    return repaired if isinstance(repaired, dict) else None


def extract_project_dir(text: str) -> Optional[str]:
    """解析 Coordinator 的 ``[run_dir]`` / ``[project_dir]`` 状态标记(沙箱 run 目录)。"""
    if not text:
        return None
    match = re.search(r"(?m)^\[(?:run_dir|project_dir)\]\s+([^\r\n]+?)\s*$", text)
    if not match:
        return None
    raw = match.group(1).strip().strip("`")
    return raw or None


def detect_hc_intent(text: str) -> Optional[str]:
    """兜底:Agent 违约时,从文本里嗅出它**想**触发的 HC 门。

    文本同时包含某个 HC 门名(G1/G2/N10/G3/G4/HC7/USER_ESCALATE)**和**至少一个意图
    关键词时,认为 Agent 在表达要触发 HC 但未落 JSON,返回该门名;调用方用空 payload 兜住。
    """
    if not text:
        return None
    hc_kind: Optional[str] = None
    for kind in HC_KINDS:
        if kind in text:
            hc_kind = kind
            break
    if hc_kind is None:
        return None
    if not any(kw in text for kw in _HC_INTENT_KEYWORDS):
        return None
    return hc_kind


def build_hc_fallback_payload(job: PipelineJob, hc_kind: str, collected_messages: list[str]) -> dict:
    """Agent 违约时的占位 HC payload:只带门名 + 兜底标记,不猜测业务字段。"""
    return {
        "hc": hc_kind,
        "mode": job.mode,
        "project_dir": job.project_dir,
        "__fallback__": True,
        "agent_message_tail": (collected_messages[-1] if collected_messages else "")[-400:],
    }


def _g3_auto_decision(payload: dict) -> tuple[str, str]:
    """Validate an independent Validator result and choose the internal G3 decision."""
    context = payload.get("review_context")
    qc = context.get("qc_report") if isinstance(context, dict) else None
    if not isinstance(qc, dict):
        raise ValueError("G3 缺少独立 Validator 结果")
    metadata = qc.get("qc_metadata") or {}
    execution_ref = qc.get("execution_ref") or {}
    subject = qc.get("subject") or {}
    input_keys = {
        item.get("key")
        for item in (metadata.get("input_artifacts") or [])
        if isinstance(item, dict) and item.get("sha256")
    }
    if (
        qc.get("validator") != "topic5-validator"
        or not subject.get("artifact_id")
        or not subject.get("sha256")
        or not qc.get("execution_source")
        or not qc.get("rules_version")
        or not qc.get("validator_version")
        or metadata.get("mode") != "independent-agent"
        or metadata.get("validator_id") != "topic5-validator"
        or not all(
            execution_ref.get(key)
            for key in ("task_id", "run_id", "attempt_id")
        )
        or not _G3_REQUIRED_INPUT_KEYS.issubset(input_keys)
    ):
        raise ValueError("G3 Validator 结果缺少独立身份、版本或完整输入绑定")

    p0_count = int(qc.get("p0_count") or 0)
    has_p0 = bool(qc.get("has_p0"))
    if has_p0 != (p0_count > 0):
        raise ValueError("G3 Validator 的 has_p0 与 p0_count 不一致")
    if not has_p0:
        return "pass", "Validator P0=0，内部自动放行"

    issues = [
        item for item in (qc.get("issues") or [])
        if isinstance(item, dict) and item.get("severity") == "P0"
    ]
    if len(issues) < p0_count:
        raise ValueError("G3 Validator 的 P0 明细少于 p0_count")
    summaries = [
        str(item.get("evidence") or item.get("title") or item.get("id") or "未命名 P0")
        for item in issues[:5]
    ]
    suffix = f"；另有 {p0_count - len(summaries)} 项" if p0_count > len(summaries) else ""
    return (
        "reject",
        f"Validator P0={p0_count}，内部自动返修：{'；'.join(summaries)}{suffix}",
    )


# ---- Runner ----------------------------------------------------------------


@dataclass
class Topic5Config:
    """topic5 运行器所需的方舟资源常量(来自 ma-resources/skill_ids.json 汇总)。"""

    coordinator_agent_id: str
    environment_id: str
    memory_store_id: str = ""  # 会被平台自动挂到沙箱 /mnt/memory/;为空则不挂
    vault_ids: tuple[str, ...] = ()
    session_timeout_sec: int = 6 * 3600


@dataclass
class _ProgressState:
    """一次 pipeline 的进度卡片本地状态(runner 内部使用)。"""

    started_at_wall: float = field(default_factory=time.time)
    tool_lines: deque = field(default_factory=lambda: deque(maxlen=MAX_TOOL_LINES))
    overflow: int = 0
    last_patch_at: float = 0.0
    last_signature: str = ""

    def push_tool_line(self, line: str) -> None:
        if len(self.tool_lines) == self.tool_lines.maxlen:
            self.overflow += 1
        self.tool_lines.append(line)


class Topic5Runner:
    """飞书 ↔ 方舟 MA topic5 pipeline 的桥接器。"""

    def __init__(
        self,
        ark: ArkClient,
        feishu: FeishuSender,
        store: PipelineStore,
        config: Topic5Config,
        loop: Optional[asyncio.AbstractEventLoop] = None,
        card_sender: Optional["Topic5CardSenderProtocol"] = None,
    ):
        self._ark = ark
        self._feishu = feishu
        self._store = store
        self._config = config
        self._loop = loop
        self._card_sender = card_sender  # 由 topic5_hitl 注入
        self._start_lock = asyncio.Lock()
        self._active_streams: dict[str, asyncio.Task] = {}
        self._progress: dict[str, "_ProgressState"] = {}
        self._card_locks: dict[str, asyncio.Lock] = {}

    def bind_card_sender(self, sender: "Topic5CardSenderProtocol") -> None:
        self._card_sender = sender

    def get_job(self, job_id: str) -> Optional[PipelineJob]:
        return self._store.get_job(job_id)

    def claim_paused_resume(self, job_id: str) -> bool:
        return self._store.claim_paused_resume(job_id)

    def restore_paused(self, job_id: str, reason: str) -> None:
        self._store.mark_paused(job_id, reason)

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
        root_message_id: str = "",
    ) -> PipelineJob:
        """串行化创建动作，但运行期按话题隔离，允许多个 Job 并发。"""
        async with self._start_lock:
            return await self._start_job_locked(
                chat_id=chat_id,
                thread_id=thread_id,
                user_open_id=user_open_id,
                mode=mode,
                user_message=user_message,
                user_vault_id=user_vault_id,
                root_message_id=root_message_id,
            )

    async def start_routed_job(
        self,
        *,
        task_id: str,
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
        if mode not in ("demo", "full"):
            raise Topic5RunnerError(f"不支持的 Topic5 mode: {mode}")
        async with self._start_lock:
            return await self._start_job_locked(
                chat_id=chat_id,
                thread_id=thread_id,
                user_open_id=user_open_id,
                mode=mode,
                user_message=user_message,
                user_vault_id=user_vault_id,
                root_message_id=root_message_id,
                task_id=task_id,
                existing_session_id=existing_session_id,
                skip_topic_guard=True,
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
        root_message_id: str = "",
        task_id: str = "",
        existing_session_id: str = "",
        skip_topic_guard: bool = False,
        on_session_created: Optional[Callable[[str], None]] = None,
        on_job_created: Optional[Callable[[PipelineJob], None]] = None,
    ) -> PipelineJob:
        # 主时间线每条触发消息都会创建独立话题，因此 thread_id 为空时不做全局门禁。
        # 若用户在已有话题内再次发触发词，只阻止该话题重复启动；其他话题不受影响。
        existing = self._store.get_active_job_by_topic(chat_id, thread_id)
        if task_id:
            task_job = self._store.get_job_by_task(task_id)
            if task_job:
                return task_job
        if existing and not skip_topic_guard:
            state = "等待审核" if existing.status == STATUS_WAIT_HC else "运行中"
            raise Topic5RunnerError(
                f"当前话题已有复盘报告任务{state}"
                f"(mode={existing.mode},phase={existing.current_phase})。"
                "请等待当前话题任务结束，或在主会话发起新任务。"
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
            env_overrides["TOPIC5_MEMORY_STORE_ID"] = self._config.memory_store_id
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
            create_kwargs["title"] = f"topicx/{task_id}/topic5"
        session_id = existing_session_id or await self._ark.create_session(
            self._config.coordinator_agent_id,
            self._config.environment_id,
            **create_kwargs,
        )
        if on_session_created is not None:
            on_session_created(session_id)
        job = self._store.create_job(
            chat_id=chat_id,
            thread_id=thread_id,
            user_open_id=user_open_id,
            ma_session_id=session_id,
            mode=mode,
            project_dir="",
            root_message_id=root_message_id,
            task_id=task_id,
        )
        if on_job_created is not None:
            on_job_created(job)
        self._store.append_receipt(
            receipt_type="message.ack",
            sender="project_agent",
            recipient="topic5_coordinator",
            job_id=job.job_id,
            correlation_id=task_id or job.job_id,
            payload={"mode": mode, "session_id": session_id},
            message_id=f"{job.job_id}:message_ack",
        )
        log.info(
            "topic5 start_job job=%s session=%s mode=%s chat=%s user=%s",
            job.job_id, session_id, mode, chat_id, user_open_id,
        )

        prompt = (
            f"[topic5] 触发词=复盘报告 mode={mode}\n"
            f"请按 topic5-coordinator system prompt 驱动 run_pipeline.py 执行复盘报告 pipeline。\n"
            f"用户原始消息:{user_message}"
        )
        state = _ProgressState()
        self._progress[job.job_id] = state
        self._card_locks[job.job_id] = asyncio.Lock()
        card = build_progress_card(
            job=job, status=STATUS_RUNNING, elapsed_sec=0, tool_lines=[], overflow=0,
        )
        try:
            if root_message_id:
                # 话题模式:首发进度卡回复触发消息并开启话题,拿回新卡 message_id + thread_id。
                message_id, topic_thread_id = await self._loop_run(
                    lambda: self._feishu.send_interactive_card_in_thread(root_message_id, card)
                )
                if topic_thread_id:
                    self._store.set_topic_thread_id(job.job_id, topic_thread_id)
                    job.topic_thread_id = topic_thread_id
            else:
                message_id = await self._loop_run(
                    lambda: self._feishu.send_interactive_card(chat_id, card)
                )
        except Exception as error:  # noqa: BLE001
            log.warning("topic5 progress card init failed job=%s: %s", job.job_id, error)
            message_id = None
        if message_id:
            self._store.set_progress_card_message_id(job.job_id, message_id)
            job.progress_card_message_id = message_id
            state.last_signature = self._signature(card)

        task = self._spawn_stream(job, first_user_message=prompt)
        self._active_streams[session_id] = task
        return job

    async def resume_job(self, job: PipelineJob, decision_message: str) -> None:
        """HITL 卡片回调路径:注入 user.message(``HCx pass/reject/remark: note``)后重订 SSE。"""
        prev = self._active_streams.get(job.ma_session_id)
        if (
            prev is not None
            and prev is not asyncio.current_task()
            and not prev.done()
        ):
            prev.cancel()
        self._store.resume_running(job.job_id, next_phase=job.current_phase)
        if job.job_id not in self._progress:
            self._progress[job.job_id] = _ProgressState()
        await self._render_and_patch(job.job_id, status=STATUS_RUNNING, force=True)
        task = self._spawn_stream(job, first_user_message=decision_message)
        self._active_streams[job.ma_session_id] = task

    async def wait_for_activity_file(self, job: PipelineJob) -> None:
        """G2 已确认后暂停在宿主侧，等待活动 Excel 到位。"""
        self._store.mark_wait_input(job.job_id)
        await self._render_and_patch(
            job.job_id, status=STATUS_WAIT_INPUT, force=True
        )
        await self.reply_for_job(
            job,
            "G2 方案已确认。请在当前话题上传活动明细 `.xlsx` 文件；"
            "挂载成功后将自动进入生产 Run。",
        )

    async def resume_after_activity_file(self, job: PipelineJob) -> None:
        """文件挂载完成后，把引用注入 G2 ConfirmedPlan 并自动续跑。"""
        if not job.source_file_id or not job.source_file_mount_path:
            raise Topic5RunnerError("活动明细文件尚未完成挂载")
        source_note = json.dumps(
            {
                "source_file": {
                    "name": job.source_file_name,
                    "mount_path": job.source_file_mount_path,
                }
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        await self.reply_for_job(
            job,
            f"数据文件已挂载：`{job.source_file_name}`。正在自动启动生产 Run。",
        )
        await self.resume_job(job, f"G2 pass: {source_note}")

    async def attach_activity_file(
        self,
        *,
        chat_id: str,
        thread_id: str,
        user_open_id: str,
        resource: ResourceRef,
    ) -> PipelineJob:
        """下载飞书话题里的活动明细 Excel，并挂到对应 MA Session。

        只允许 G2 确认后的 input 阶段上传；同一任务可重传，最后一次成功挂载的
        唯一 mount_path 会写入 pipeline_jobs，并注入 ConfirmedPlan。
        """
        job = self._store.get_active_job_by_session_key(
            chat_id, thread_id, user_open_id
        )
        if job is None:
            raise Topic5RunnerError("当前话题没有可接收数据文件的活跃任务")
        if job.status != STATUS_WAIT_INPUT:
            raise Topic5RunnerError("请先完成 G1 需求确认和 G2 方案确认，再上传活动明细")
        file_name = (resource.file_name or "").strip()
        if not file_name.lower().endswith(".xlsx"):
            raise Topic5RunnerError("活动明细仅支持 .xlsx 文件")
        message_id = resource.message_id
        if not message_id:
            raise Topic5RunnerError("文件消息缺少 message_id，无法下载附件")

        try:
            data = await self._loop_run(
                lambda: self._feishu.download_resource(
                    message_id, resource.file_key, resource.type
                )
            )
        except Exception as error:  # noqa: BLE001
            log.exception(
                "topic5 activity file download failed job=%s name=%s",
                job.job_id,
                file_name,
            )
            raise Topic5RunnerError("Excel 下载失败，请稍后重试") from error
        if not data:
            raise Topic5RunnerError("上传的 Excel 文件为空")
        if len(data) > MAX_ACTIVITY_XLSX_BYTES:
            raise Topic5RunnerError("Excel 文件超过 50 MiB 上限")
        try:
            validate_xlsx_container(data)
        except IntakeValidationError as error:
            raise Topic5RunnerError(str(error)) from error

        mount_path = (
            f"topic5_activity_detail_{job.job_id}_{uuid.uuid4().hex[:8]}.xlsx"
        )
        try:
            ark_file_id = await self._ark.upload_file(
                file_name,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                data,
            )
            await self._ark.add_session_file(
                job.ma_session_id, ark_file_id, mount_path
            )
        except Exception as error:  # noqa: BLE001
            log.exception(
                "topic5 activity file ark mount failed job=%s name=%s",
                job.job_id,
                file_name,
            )
            raise Topic5RunnerError("Excel 挂载到任务环境失败，请稍后重试") from error
        self._store.set_source_file(
            job.job_id, ark_file_id, file_name, mount_path
        )
        refreshed = self._store.get_job(job.job_id)
        if refreshed is None:
            raise Topic5RunnerError("文件已挂载，但任务状态读取失败")
        log.info(
            "topic5 activity file attached job=%s name=%s mount=%s bytes=%s",
            job.job_id,
            file_name,
            mount_path,
            len(data),
        )
        return refreshed

    async def attach_requirement_payload(
        self, job: PipelineJob, requirement: dict, event_id: int
    ) -> str:
        """Mount the exact G1 form JSON so the LLM only transports a short path."""
        mount_path = f"topic5_requirement_{job.job_id}_{event_id}.json"
        data = json.dumps(
            requirement, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        try:
            ark_file_id = await self._ark.upload_file(
                mount_path, "application/json", data
            )
            await self._ark.add_session_file(
                job.ma_session_id, ark_file_id, mount_path
            )
        except Exception as error:  # noqa: BLE001
            log.exception(
                "topic5 requirement payload mount failed job=%s event=%s",
                job.job_id,
                event_id,
            )
            raise Topic5RunnerError(
                "需求问卷挂载到任务环境失败，请稍后重试"
            ) from error
        return f"@/mnt/session/uploads/{mount_path}"

    async def reply_for_job(self, job: PipelineJob, text: str) -> None:
        """向任务所属话题发送文本回执；老任务无 root 时回退主会话。"""
        await self._reply_async(
            job.chat_id, text, root_message_id=job.root_message_id
        )

    async def reply_to_message(self, message_id: str, text: str) -> None:
        """回复一条入站消息并保持在其话题内。"""
        await self._loop_run(
            lambda: self._feishu.reply_in_thread(message_id, text)
        )

    async def continue_job(
        self, chat_id: str, thread_id: str, user_open_id: str, message: str = "继续"
    ) -> Optional[PipelineJob]:
        """/continue 续跑:把一条 user.message 注入最近一个可续跑的 Session,重订 SSE。"""
        job = self._store.get_resumable_job_by_session_key(chat_id, thread_id, user_open_id)
        if job is None:
            return None
        if job.failure_scope not in ("", "recoverable"):
            raise Topic5RunnerError("当前任务已终止，不能继续")
        session = await self._ark.get_session(job.ma_session_id)
        session_status = session_status_from_payload(session)
        if not is_session_resumable(session_status):
            self._store.mark_failed(
                job.job_id,
                f"session_not_resumable:{session_status or 'unknown'}",
                failure_scope=FAILURE_SCOPE_SESSION,
                retry_strategy="",
            )
            raise Topic5RunnerError(
                f"业务 Session 状态为 {session_status or 'unknown'}，不能继续当前任务"
            )
        if job.retry_strategy == RETRY_STRATEGY_NODE:
            bundle = (
                Path(__file__).resolve().parent
                / "tools" / "out" / "pipeline-orchestrator.zip"
            )
            if not bundle.is_file():
                raise Topic5RunnerError(
                    "缺少已验证的 pipeline-orchestrator 恢复包，请先部署 Topic5 Skill"
                )
            data = bundle.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            mount_name = (
                f"topic5_retry_{digest[:12]}_{int(time.time())}.zip"
            )
            file_id = await self._ark.upload_file(
                mount_name, "application/zip", data
            )
            await self._ark.add_session_file(
                job.ma_session_id, file_id, mount_name
            )
            match = re.search(
                r"(?:^|/)tasks/([^/]+)/runs/([^/]+)(?:/|$)",
                job.project_dir,
            )
            if not match:
                raise Topic5RunnerError("任务缺少可恢复的 durable run 路径")
            task_id, run_id = match.groups()
            message = (
                f"[topic5-control] retry_failed node={job.current_phase}\n"
                "使用下面已验证的恢复包，只重试 durable run 当前失败节点；"
                "保留既有产物、审核记录和 run_id。\n"
                f"BUNDLE=/mnt/session/uploads/{mount_name}\n"
                f"BUNDLE_SHA256={digest}\n"
                "TARGET=/tmp/topic5-pipeline-retry-${BUNDLE_SHA256}\n"
                "echo \"$BUNDLE_SHA256  $BUNDLE\" | sha256sum -c - && "
                "rm -rf \"$TARGET\" && mkdir -p \"$TARGET\" && "
                "unzip -q \"$BUNDLE\" -d \"$TARGET\"\n"
                "cd \"$TARGET/pipeline-orchestrator\" && "
                "nohup python3 run_pipeline.py retry "
                "--dag contracts/pipeline.v0.2.yaml "
                "--workspace /workspace/ma-workspaces/projects/topic5 "
                f"--task {task_id} --run {run_id} "
                f"> /workspace/ma-workspaces/projects/topic5/tasks/{task_id}/"
                f"runs/{run_id}/retry.log 2>&1 &\n"
                "校验失败不得执行。"
            )
        log.info(
            "topic5 /continue resume job=%s session=%s session_status=%s "
            "prev_status=%s phase=%s",
            job.job_id, job.ma_session_id, session_status,
            job.status, job.current_phase,
        )
        await self.resume_job(job, message)
        return job

    async def cancel_active_job(
        self, chat_id: str, thread_id: str, user_open_id: str
    ) -> Optional[PipelineJob]:
        """取消该会话键上的活跃 job(SSE task + status → failed),让 /new 可以马上开新任务。"""
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
        await self._render_and_patch(job.job_id, status=STATUS_FAILED, error=reason, force=True)
        return job

    async def recover_active_jobs(self) -> dict[str, str]:
        """恢复运行态任务的 SSE；paused 不在 list_active_jobs 中，不自动恢复。"""
        results: dict[str, str] = {}
        for job in self._store.list_paused_jobs():
            await self._render_and_patch(
                job.job_id, status=STATUS_PAUSED, force=True
            )
            results[job.job_id] = STATUS_PAUSED
        for job in self._store.list_active_jobs():
            try:
                results[job.job_id] = await asyncio.wait_for(
                    self.recover_job(job),
                    timeout=RECOVERY_JOB_TIMEOUT_SEC,
                )
            except asyncio.TimeoutError:
                log.error(
                    "topic5 startup recovery timed out job=%s session=%s timeout=%ss",
                    job.job_id,
                    job.ma_session_id,
                    RECOVERY_JOB_TIMEOUT_SEC,
                )
                results[job.job_id] = "recovery_timeout"
            except Exception as error:  # noqa: BLE001
                log.exception(
                    "topic5 startup recovery failed job=%s session=%s: %s",
                    job.job_id, job.ma_session_id, error,
                )
                results[job.job_id] = "recovery_error"
        return results

    async def recover_job(self, job: PipelineJob) -> str:
        """根据历史事件恢复一个本地活跃任务,返回已采取的恢复动作。"""
        if job.status == STATUS_WAIT_INPUT:
            await self._render_and_patch(
                job.job_id, status=STATUS_WAIT_INPUT, force=True
            )
            return STATUS_WAIT_INPUT
        events = await self._ark.list_session_events(job.ma_session_id)
        if job.status == STATUS_STOPPING:
            return "stopping"
        turn_events = _latest_turn_events(events)
        agent_messages = [
            body
            for event in turn_events
            if event.get("type") == "agent.message"
            for body in [event_text(event)]
            if body
        ]
        messages = [
            body
            for event in turn_events
            for body in [
                event_text(event)
                if event.get("type") == "agent.message"
                else _event_contract_text(event)
            ]
            if body
        ]
        for body in agent_messages:
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
            (index for index, event in enumerate(events) if event.get("type") in terminal_types),
            default=-1,
        )
        if last_terminal_index == -1 or last_terminal_index < len(events) - 1:
            self._restore_stream(job)
            return "subscribed"

        terminal = events[last_terminal_index]
        terminal_type = terminal.get("type") or ""
        if terminal_type == "user.interrupt":
            reason = "用户在方舟后台手动停止了 Session"
            self._store.mark_stopped(job.job_id, reason)
            await self._render_and_patch(job.job_id, status=STATUS_STOPPED, error=reason, force=True)
            return "stopped"
        if terminal_type in {"session.error", "session.status_failed", "session.status_terminated"}:
            error = event_error(terminal) or (
                "session_terminated" if terminal_type == "session.status_terminated" else "session_failed"
            )
            self._store.mark_failed(
                job.job_id,
                error[:400],
                failure_scope=FAILURE_SCOPE_SESSION,
            )
            await self._render_and_patch(job.job_id, status=STATUS_FAILED, error=error[:400], force=True)
            return "failed"

        await self._handle_idle(job.job_id, messages, recovering=True)
        current = self._store.get_job(job.job_id)
        return current.status if current is not None else "missing"

    def _restore_stream(self, job: PipelineJob) -> None:
        existing = self._active_streams.get(job.ma_session_id)
        if existing is not None and not existing.done():
            return
        self._progress.setdefault(
            job.job_id, _ProgressState(started_at_wall=float(job.started_at))
        )
        self._card_locks.setdefault(job.job_id, asyncio.Lock())
        task = self._spawn_stream(job, first_user_message=None)
        self._active_streams[job.ma_session_id] = task
        log.info("topic5 restored SSE subscription job=%s session=%s", job.job_id, job.ma_session_id)

    # ---- SSE 消费 ----------------------------------------------------------

    def _spawn_stream(self, job: PipelineJob, first_user_message: Optional[str]) -> asyncio.Task:
        loop = self._loop or asyncio.get_event_loop()
        return loop.create_task(self._drive_session(job, first_user_message))

    async def _drive_session(self, job: PipelineJob, first_user_message: Optional[str]) -> None:
        """消费 MA 事件流;瞬时网络断线时重连但不重复发送 user.message。"""
        session_id = job.ma_session_id
        job_id = job.job_id
        sent_initial_message = first_user_message is None
        seen_event_ids: set[str] = set()
        try:
            history = await self._ark.list_session_events(session_id)
            seen_event_ids.update(
                str(event["id"])
                for event in history
                if isinstance(event.get("id"), str) and event.get("id")
            )
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
                        raise RuntimeError("方舟事件流意外结束,未观察到 Session 终态")
                except asyncio.CancelledError:
                    raise
                except Exception:
                    if attempt >= SSE_RECONNECT_MAX_ATTEMPTS:
                        raise
                    delay = min(
                        SSE_RECONNECT_BASE_DELAY_SEC * (2**attempt),
                        SSE_RECONNECT_MAX_DELAY_SEC,
                    )
                    delay *= random.uniform(0.8, 1.2)
                    log.warning(
                        "topic5 SSE disconnected; reconnecting job=%s session=%s attempt=%s/%s delay=%.1fs",
                        job_id, session_id, attempt + 1, SSE_RECONNECT_MAX_ATTEMPTS, delay,
                    )
                    await asyncio.sleep(delay)
        except asyncio.CancelledError:
            log.info("topic5 stream cancelled job=%s session=%s", job_id, session_id)
            raise
        except Exception as error:  # noqa: BLE001
            log.exception("topic5 stream failed job=%s: %s", job_id, error)
            reason = f"sse_stream_error: {error!s}"[:400]
            self._store.mark_failed(job_id, reason)
            await self._render_and_patch(job_id, status=STATUS_FAILED, error=reason, force=True)
        finally:
            if self._active_streams.get(session_id) is asyncio.current_task():
                self._active_streams.pop(session_id, None)

    async def _consume_events(
        self, job_id: str, stream, *, seen_event_ids: Optional[set[str]] = None,
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

            phase_changed = False
            if etype == "user.message":
                collected_messages.clear()
            elif etype == "agent.message":
                body = event_text(event)
                if body:
                    collected_messages.append(body)
                    self._maybe_update_project_dir(job_id, body)
                    phase_changed = self._maybe_update_phase(job_id, body)
            else:
                contract_body = _event_contract_text(event)
                if contract_body:
                    collected_messages.append(contract_body)

            progress = event_progress(event)
            if progress:
                self._append_progress_line(job_id, progress)

            if progress or phase_changed:
                await self._render_and_patch(job_id, status=STATUS_RUNNING, force=phase_changed)

            if etype == "user.interrupt":
                current = self._store.get_job(job_id)
                if current and current.status == STATUS_STOPPING:
                    continue
                reason = "用户在方舟后台手动停止了 Session"
                self._store.mark_stopped(job_id, reason)
                await self._render_and_patch(job_id, status=STATUS_STOPPED, error=reason, force=True)
                return True

            if etype in ("session.error", "session.status_failed", "session.status_terminated"):
                error = event_error(event) or (
                    "session_terminated" if etype == "session.status_terminated" else "session_failed"
                )
                log.error("topic5 session terminal job=%s etype=%s error=%s", job_id, etype, error[:400])
                self._store.mark_failed(
                    job_id,
                    error[:400],
                    failure_scope=FAILURE_SCOPE_SESSION,
                )
                await self._render_and_patch(job_id, status=STATUS_FAILED, error=error[:400], force=True)
                return True

            if etype == "session.status_idle" and not event_requires_action(event):
                current = self._store.get_job(job_id)
                if current and current.status == STATUS_STOPPING:
                    return True
                await self._handle_idle(
                    job_id, collected_messages
                )
                return True
        return False

    async def _handle_idle(self, job_id: str, collected: list[str], *, recovering: bool = False) -> None:
        """Session 空闲了:看最后一条 agent.message 是否落了 HC JSON。"""
        job = self._store.get_job(job_id)
        if not job:
            log.warning("topic5 handle_idle 找不到 job=%s", job_id)
            return
        if job.status in (STATUS_STOPPING, STATUS_PAUSED):
            log.info(
                "topic5 ignores stale idle while pausing job=%s status=%s",
                job_id,
                job.status,
            )
            return
        last = collected[-1] if collected else ""
        envelope = next(
            (value for text in reversed(collected)
             if (value := extract_final_envelope(text))),
            {},
        )
        if not envelope:
            envelope = extract_nonstructured_failure(collected)
        if envelope.get("status") == "failed":
            reason = str(
                envelope.get("reason") or envelope.get("error")
                or "Durable pipeline failed"
            )[:400]
            node = str(envelope.get("node") or "")
            if node:
                self._store.update_phase(job_id, node)
            self._store.mark_failed(
                job_id,
                reason,
                retry_strategy=RETRY_STRATEGY_NODE,
            )
            await self._render_and_patch(
                job_id, status=STATUS_FAILED, error=reason, force=True
            )
            return
        # A terminal result supersedes HC envelopes read from historical logs.
        payload = None if envelope else _best_hc_payload(collected)
        if payload is None and not envelope:
            intent_hc = detect_hc_intent(last)
            if intent_hc is not None:
                log.warning(
                    "topic5 HC fallback triggered job=%s hc=%s (agent did not emit JSON)",
                    job_id, intent_hc,
                )
                payload = build_hc_fallback_payload(job, intent_hc, collected)
        if payload:
            hc_kind = str(payload["hc"])
            if hc_kind == "G1":
                self._store.append_receipt(
                    receipt_type="task.accepted",
                    sender="topic5_coordinator",
                    recipient="project_agent",
                    job_id=job_id,
                    correlation_id=job_id,
                    causation_id=f"{job_id}:message_ack",
                    payload={
                        "session_id": job.ma_session_id,
                        "mode": job.mode,
                        "run_id": payload.get("run_id"),
                    },
                    message_id=f"{job_id}:task_accepted",
                )
            elif hc_kind == "G3":
                self._store.append_receipt(
                    receipt_type="artifact.ready",
                    sender="topic5_coordinator",
                    recipient="project_agent",
                    job_id=job_id,
                    correlation_id=job_id,
                    payload=(payload.get("review_context") or {}),
                    message_id=f"{job_id}:artifact_ready:{payload.get('run_id', '')}:"
                    f"{((payload.get('review_context') or {}).get('report_ref') or {}).get('artifact_id', '')}",
                )
                try:
                    decision, note = _g3_auto_decision(payload)
                except (TypeError, ValueError) as error:
                    reason = f"G3 内部 Validator 契约校验失败：{error!s}"
                    log.error("topic5 internal G3 rejected job=%s error=%s", job_id, reason)
                    self._store.mark_failed(job_id, reason)
                    await self._render_and_patch(
                        job_id, status=STATUS_FAILED, error=reason, force=True
                    )
                    return
                self._append_progress_line(job_id, note)
                log.info(
                    "topic5 internal G3 auto-resume job=%s decision=%s note=%s",
                    job_id, decision, note[:300],
                )
                await self.resume_job(job, f"G3 {decision}: {note}")
                return
            elif hc_kind == "G4":
                self._store.append_receipt(
                    receipt_type="delivery.ready",
                    sender="topic5_coordinator",
                    recipient="project_agent",
                    job_id=job_id,
                    correlation_id=job_id,
                    payload=(payload.get("review_context") or {}),
                    message_id=f"{job_id}:delivery_ready:{payload.get('run_id', '')}:"
                    f"{((payload.get('review_context') or {}).get('delivery_ref') or {}).get('artifact_id', '')}",
                )
            existing_hc = self._store.latest_hc_for_job(job_id, hc_kind)
            if recovering and existing_hc and existing_hc.resolved_at is None:
                if _hc_payload_score(payload) > _hc_payload_score(existing_hc.payload):
                    self._store._conn.execute(  # noqa: SLF001 - runner 与 store 同包
                        "UPDATE pipeline_hc_events SET payload = ? WHERE id = ?",
                        (
                            json.dumps(payload, ensure_ascii=False),
                            existing_hc.id,
                        ),
                    )
                    existing_hc = (
                        self._store.latest_hc_for_job(job_id, hc_kind)
                        or existing_hc
                    )
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
                    await self._render_and_patch(job_id, status=STATUS_WAIT_HC, force=True)
                log.info(
                    "topic5 restored pending hc card job=%s hc=%s event=%s",
                    job_id, hc_kind, existing_hc.id,
                )
                return
            event_id = self._store.append_hc_event(job_id, hc_kind, payload)
            self._store.mark_wait_hc(job_id, hc_kind)
            job = self._store.get_job(job_id) or job
            if self._card_sender is None:
                await self._render_and_patch(job_id, status=STATUS_WAIT_HC, force=True)
                await self._reply_async_by_job(
                    job_id,
                    f"⏸️ 等你审 {hc_kind}(卡片发送器未注入,发送 /continue 注入「继续」推进该阶段)",
                )
                return
            try:
                message_id = await self._card_sender.send_hc_card(job, hc_kind, event_id, payload)
                if message_id:
                    self._store._conn.execute(  # noqa: SLF001 - runner 与 store 同包
                        "UPDATE pipeline_hc_events SET card_message_id = ? WHERE id = ?",
                        (message_id, event_id),
                    )
            except Exception as error:  # noqa: BLE001
                log.exception("topic5 send hc card failed job=%s hc=%s: %s", job_id, hc_kind, error)
                await self._render_and_patch(job_id, status=STATUS_WAIT_HC, force=True)
                await self._reply_async_by_job(
                    job_id, f"⚠️ {hc_kind} 卡片发送失败({error!s});可回复 pass/reject 手工继续"
                )
            return

        # 终态:成功则末尾消息带飞书报告 URL,否则停止。
        online_url = _validated_envelope_url(envelope)
        if (
            not online_url
            or not envelope.get("handoff_validated")
            or not envelope.get("terminal_accepted")
            or envelope.get("degraded") is not False
            or envelope.get("revision_id") is None
            or envelope.get("live_revision_id") != envelope.get("revision_id")
            or not all(
                envelope.get(key)
                for key in (
                    "report_ref",
                    "delivery_ref",
                    "validation_ref",
                    "user_acceptance_ref",
                    "run_id",
                    "task_id",
                )
            )
        ):
            reason = (
                "Session 已结束，但 FINAL_RESULT 未同时满足真实飞书 URL、"
                "可信 handoff、实时 revision、版本引用和非降级要求"
            )
            self._store.mark_stopped(job_id, reason)
            await self._render_and_patch(job_id, status=STATUS_STOPPED, error=reason, force=True)
            return
        self._store.append_receipt(
            receipt_type="deliverable.accepted",
            sender="project_agent",
            recipient="topic5_coordinator",
            job_id=job_id,
            correlation_id=job_id,
            payload=envelope,
            message_id=f"{job_id}:deliverable_accepted:"
            f"{envelope.get('report_version', '')}:"
            f"{envelope.get('revision_id', '')}",
        )
        self._store.mark_done(job_id, online_url=online_url)
        await self._render_and_patch(job_id, status=STATUS_DONE, online_url=online_url, force=True)
        self._progress.pop(job_id, None)

    # ---- helper --------------------------------------------------------------

    def _maybe_update_phase(self, job_id: str, body: str) -> bool:
        """扫 body 里的 `[phase] X` 前缀更新 current_phase;有变化返回 True。"""
        match = re.search(r"\[phase\]\s+([A-Za-z0-9_]+)", body)
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
                "topic5 project_dir changed job=%s old=%s new=%s",
                job_id, job.project_dir, project_dir,
            )
        self._store.update_project_dir(job_id, project_dir)
        log.info("topic5 project_dir resolved job=%s path=%s", job_id, project_dir)
        return True

    def _append_progress_line(self, job_id: str, progress: str) -> None:
        state = self._progress.get(job_id)
        if state is None:
            return
        ts = time.strftime("%H:%M:%S", time.localtime())
        state.push_tool_line(f"{ts} · {progress}")

    def _review_lines(self, job_id: str) -> list[str]:
        return format_review_lines(
            self._store.list_resolved_hc_for_job(job_id)
        )

    async def _render_and_patch(
        self, job_id: str, *, status: str, error: str = "", online_url: str = "", force: bool = False,
    ) -> None:
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
            return
        try:
            await self.patch_job_card(job_id, card)
        except Exception as error_patch:  # noqa: BLE001
            log.warning("topic5 progress card patch failed job=%s: %s", job_id, error_patch)
            return
        state.last_patch_at = now
        state.last_signature = signature

    async def patch_job_card(self, job_id: str, card: dict) -> Optional[str]:
        job = self._store.get_job(job_id)
        if job is None or not job.progress_card_message_id:
            return None
        lock = self._card_locks.setdefault(job_id, asyncio.Lock())
        async with lock:
            await self._loop_run(
                lambda: self._feishu.patch_interactive_card(job.progress_card_message_id, card)
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
        loop = self._loop or asyncio.get_event_loop()
        return await loop.run_in_executor(None, fn)

    async def _reply_async_by_job(self, job_id: str, text: str) -> None:
        job = self._store.get_job(job_id)
        if job:
            await self._reply_async(job.chat_id, text, root_message_id=job.root_message_id)

    async def _reply_async(
        self, chat_id: str, text: str, root_message_id: str = ""
    ) -> None:
        loop = self._loop or asyncio.get_event_loop()
        try:
            if root_message_id:
                # 话题模式:文本回执回复触发消息,落进同一话题(reply_in_thread 已建的话题)。
                await loop.run_in_executor(
                    None, lambda: self._feishu.reply_in_thread(root_message_id, text)
                )
            else:
                await loop.run_in_executor(
                    None, lambda: self._feishu.send_to_chat(chat_id, text)
                )
        except Exception as error:  # noqa: BLE001
            log.warning("topic5 reply failed chat=%s: %s", chat_id, error)


class Topic5RunnerError(RuntimeError):
    """topic5 触发/续跑相关的业务错误。"""


class Topic5CardSenderProtocol:
    """topic5_hitl 会实现的审核态切换接口(避免 runner ↔ hitl 循环 import)。"""

    async def send_hc_card(
        self, _job: PipelineJob, _hc_kind: str, _event_id: int, _payload: dict,
    ) -> Optional[str]:
        raise NotImplementedError


def _validated_envelope_url(envelope: dict) -> str:
    candidate = str(envelope.get("online_url") or "").strip()
    parsed = urlsplit(candidate)
    hostname = (parsed.hostname or "").lower()
    is_feishu = any(
        hostname == domain or hostname.endswith("." + domain)
        for domain in ("feishu.cn", "feishu.net", "larksuite.com", "larkoffice.com")
    )
    is_user_facing = hostname not in {"open.feishu.cn", "open.larksuite.com"}
    if (
        parsed.scheme == "https"
        and is_feishu
        and is_user_facing
        and "/docx/" in parsed.path
    ):
        return candidate
    return ""


def extract_final_online_url(text: str) -> str:
    """Compatibility wrapper: URL must come from the same FINAL_RESULT object."""
    return _validated_envelope_url(extract_final_envelope(text))


def extract_final_envelope(text: str) -> dict:
    """Return the single durable-workflow FINAL_RESULT object."""
    if not text:
        return {}
    for line in text.splitlines():
        if not line.startswith("FINAL_RESULT "):
            continue
        try:
            value = json.loads(line[len("FINAL_RESULT "):])
        except json.JSONDecodeError:
            continue
        if (
            isinstance(value, dict)
            and (
                value.get("status") == "failed"
                or (value.get("status") == "completed" and "online_url" in value)
            )
        ):
            return value
    return {}


def extract_nonstructured_failure(texts: list[str]) -> dict:
    """Recover a failed durable-run result when the Coordinator omitted FINAL_RESULT.

    This fallback is intentionally strict: the current turn must contain an explicit
    ``status=failed``/``status: failed`` marker and a safe node identifier.
    """
    node_patterns = (
        r"\bstatus\s*=\s*failed\s+node\s*=\s*([A-Za-z0-9_-]+)",
        r"\bstatus\s*:\s*failed\b[\s\S]{0,160}?\bcurrent_node\s*:\s*([A-Za-z0-9_-]+)",
    )
    for text in reversed(texts):
        if not re.search(r"\bstatus\s*[:=]\s*failed\b", text, re.IGNORECASE):
            continue
        node = ""
        for pattern in node_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                node = match.group(1)
                break
        if not node:
            continue
        reason_match = re.search(
            r"\*\*Failure Reason\*\*\s*:\s*(.+?)(?:\n\s*\n|\Z)",
            text,
            re.IGNORECASE | re.DOTALL,
        )
        reason = (
            reason_match.group(1).strip()
            if reason_match else f"Durable pipeline failed at {node}"
        )
        reason = re.sub(r"\s+", " ", reason)[:400]
        return {"status": "failed", "node": node, "reason": reason}
    return {}
