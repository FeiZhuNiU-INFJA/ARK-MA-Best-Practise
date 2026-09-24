"""topic6 HITL 卡片与回调:HC1 / HC2 / HC3 三种审核卡片的构造与按钮回调处理。

配合 :mod:`topic6_runner` 用:

  - Runner 在 Session 进入 idle 且末尾消息带 ``{"hc":"HCx", ...}`` 时,调
    ``Topic6Hitl.send_hc_card`` 把审核卡片发到当前 job 的 chat_id 里,并把返回的
    飞书 ``message_id`` 记到 ``pipeline_hc_events.card_message_id``。
  - 用户点卡片按钮后,飞书 SDK 通过 ``Events.CARD_ACTION`` 触发 gateway 侧回调;
    orchestrator 会把 payload 转交给 :meth:`Topic6Hitl.handle_card_action`。这里
    解出 ``action.value`` 里携带的 job_id / event_id / decision,反查 pipeline_hc_events,
    再调 runner.resume_job 让 MA Session 继续跑。

卡片模板:统一用飞书 schema=2.0 交互卡片。三个 HC 的头部颜色区分:HC1 蓝、HC2 绿、
HC3 紫。按钮固定「通过 / 打回 / 备注」三个;备注按钮走 input 交互(飞书原生 form 提交
的 form_value 里带 remark_note)。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Optional

from .pipeline_store import HcEvent, PipelineJob, PipelineStore
from .topic6_runner import Topic6CardSenderProtocol, Topic6Runner

log = logging.getLogger("arkagent.topic6.hitl")

# 决策枚举:与卡片按钮 value.decision 完全一致。
DECISION_PASS = "pass"
DECISION_REJECT = "reject"
DECISION_REMARK = "remark"

# 每个 HC 的头部信息(颜色 + 图标 + 标题)。
_HC_META = {
    "HC1": {
        "template": "blue",
        "icon": "checkbox-checked_outlined",
        "title": "HC1 · 小样本人工审核",
        "subtitle": "test 模式跑完前 500 条,需要人工确认标注质量",
    },
    "HC2": {
        "template": "green",
        "icon": "check-square_outlined",
        "title": "HC2 · 全量分布审核",
        "subtitle": "full 模式跑完全量数据,需要人工核对分布/异常",
    },
    "HC3": {
        "template": "purple",
        "icon": "review_outlined",
        "title": "HC3 · 报告终审",
        "subtitle": "报告已生成,请在飞书文档里做微调后点通过",
    },
}


# ---- 卡片构造 --------------------------------------------------------------


def _fmt_payload_line(label: str, value: Any) -> Optional[dict]:
    """把一个 payload 字段渲染成 markdown 一行,值为空则忽略,避免卡片显得稀。"""
    if value in (None, "", [], {}):
        return None
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False)
        if len(text) > 300:
            text = text[:300] + "…"
        return {"tag": "markdown", "content": f"**{label}**\n```\n{text}\n```"}
    return {"tag": "markdown", "content": f"**{label}**:{value}"}


def _payload_summary_elements(payload: dict) -> list[dict]:
    """把 HC payload 的主要字段拍成一段可读摘要。字段挑选与三档 HC 通用。"""
    fields = [
        ("模式", payload.get("mode")),
        ("项目目录", payload.get("project_dir")),
        ("宽表", payload.get("wide_table_path")),
        ("分布摘要", payload.get("distribution_summary")),
        ("发现问题", payload.get("issues_detected")),
        ("飞书文档", payload.get("feishu_doc_url")),
        ("妙搭页面", payload.get("miaoda_page_url")),
    ]
    elements: list[dict] = []
    for label, value in fields:
        node = _fmt_payload_line(label, value)
        if node:
            elements.append(node)
    return elements


def _action_value(job_id: str, hc_kind: str, event_id: int, decision: str) -> dict:
    """卡片按钮 value 结构;回调时会原样送回来。"""
    return {
        "job_id": job_id,
        "hc": hc_kind,
        "event_id": event_id,
        "decision": decision,
    }


def build_hc_card(job: PipelineJob, hc_kind: str, event_id: int, payload: dict) -> dict:
    """构造一张 HC 交互卡片(schema=2.0)。三档 HC 共用一套骨架,靠 _HC_META 区分。"""
    meta = _HC_META.get(hc_kind, _HC_META["HC1"])
    elements: list[dict] = [
        {
            "tag": "markdown",
            "content": (
                f"任务 ID:`{job.job_id}`  ·  运行模式:**{job.mode}**  ·  "
                f"当前阶段:**{job.current_phase}**"
            ),
        },
    ]
    elements.extend(_payload_summary_elements(payload))
    elements.append({"tag": "hr"})
    elements.append(
        {
            "tag": "column_set",
            "columns": [
                {
                    "tag": "column",
                    "width": "weighted",
                    "weight": 1,
                    "elements": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "通过"},
                            "type": "primary_filled",
                            "width": "fill",
                            "behaviors": [
                                {
                                    "type": "callback",
                                    "value": _action_value(
                                        job.job_id, hc_kind, event_id, DECISION_PASS
                                    ),
                                }
                            ],
                        }
                    ],
                },
                {
                    "tag": "column",
                    "width": "weighted",
                    "weight": 1,
                    "elements": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "打回"},
                            "type": "danger_filled",
                            "width": "fill",
                            "behaviors": [
                                {
                                    "type": "callback",
                                    "value": _action_value(
                                        job.job_id, hc_kind, event_id, DECISION_REJECT
                                    ),
                                }
                            ],
                        }
                    ],
                },
                {
                    "tag": "column",
                    "width": "weighted",
                    "weight": 1,
                    "elements": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "备注"},
                            "type": "default",
                            "width": "fill",
                            "behaviors": [
                                {
                                    "type": "callback",
                                    "value": _action_value(
                                        job.job_id, hc_kind, event_id, DECISION_REMARK
                                    ),
                                }
                            ],
                        }
                    ],
                },
            ],
        }
    )
    elements.append(
        {
            "tag": "markdown",
            "content": (
                "> 通过:pipeline 继续跑下一阶段  "
                "  ·  打回:标 failed 结束任务  "
                "  ·  备注:再次@bot 附一条说明后视为通过"
            ),
        }
    )

    return {
        "schema": "2.0",
        "config": {"width_mode": "default"},
        "header": {
            "title": {"tag": "plain_text", "content": meta["title"]},
            "subtitle": {"tag": "plain_text", "content": meta["subtitle"]},
            "template": meta["template"],
            "icon": {"tag": "standard_icon", "token": meta["icon"]},
        },
        "body": {"elements": elements},
    }


def build_hc1_card(job: PipelineJob, event_id: int, payload: dict) -> dict:
    return build_hc_card(job, "HC1", event_id, payload)


def build_hc2_card(job: PipelineJob, event_id: int, payload: dict) -> dict:
    return build_hc_card(job, "HC2", event_id, payload)


def build_hc3_card(job: PipelineJob, event_id: int, payload: dict) -> dict:
    return build_hc_card(job, "HC3", event_id, payload)


# ---- HITL 主类 -------------------------------------------------------------


@dataclass
class Topic6HitlDeps:
    store: PipelineStore
    runner: Topic6Runner


class Topic6Hitl(Topic6CardSenderProtocol):
    """topic6 的 HITL 门面:发卡片 + 收回调。

    - ``send_hc_card`` 由 runner 主动调用(见 :meth:`Topic6Runner._handle_idle`),
      同步走 FeishuSender.send_interactive_card,拿 message_id 返回给 runner。
    - ``handle_card_action`` 由 orchestrator 在 SDK 的 ``cardAction`` 回调里调用,
      异步在事件循环内完成:反查 → resolve_hc_event → resume_job。
    """

    def __init__(self, deps: Topic6HitlDeps):
        self._store = deps.store
        self._runner = deps.runner

    # ---- SendHcCard(runner → hitl)----------------------------------------

    async def send_hc_card(
        self,
        _job: PipelineJob,
        _hc_kind: str,
        _event_id: int,
        _payload: dict,
    ) -> Optional[str]:
        card = build_hc_card(_job, _hc_kind, _event_id, _payload)
        import asyncio

        loop = asyncio.get_event_loop()
        # FeishuSender 是同步 SDK;放到 executor 里,避免堵住 SSE 消费循环。
        try:
            message_id = await loop.run_in_executor(
                None,
                lambda: self._runner._feishu.send_interactive_card(  # noqa: SLF001 - 网关内包
                    _job.chat_id, card
                ),
            )
        except Exception as error:  # noqa: BLE001 - 卡片失败让 runner 兜底
            log.exception(
                "topic6 send_hc_card failed job=%s hc=%s: %s",
                _job.job_id, _hc_kind, error,
            )
            raise
        log.info(
            "topic6 hc card sent job=%s hc=%s message_id=%s",
            _job.job_id, _hc_kind, message_id,
        )
        return message_id

    # ---- HandleCardAction(orchestrator → hitl)----------------------------

    async def handle_card_action(self, action: Any) -> None:
        """SDK 的 ``cardAction`` 回调入口。

        ``action`` 是 lark_channel 归一化后的 ``CardActionEvent``,主要用到:
          - ``action.action.value``:我们塞进去的 dict(job_id/hc/event_id/decision)
          - ``action.message_id``:卡片 message_id(兜底反查)
          - ``action.operator.open_id``:点按钮的人;做归属校验用
        """
        payload = _extract_action_value(action)
        if not payload:
            log.warning("topic6 handle_card_action: 无 value,忽略。raw=%r", action)
            return

        decision = str(payload.get("decision") or "").strip().lower()
        event_id = payload.get("event_id")
        job_id = payload.get("job_id") or ""
        hc_kind = payload.get("hc") or ""

        # 反查 HC 记录:优先 event_id;拿不到再 fallback 卡片 message_id。
        hc_event: Optional[HcEvent] = None
        if isinstance(event_id, int) or (isinstance(event_id, str) and event_id.isdigit()):
            hc_event = self._store.get_hc_event(int(event_id))
        if hc_event is None:
            card_message_id = getattr(action, "message_id", "") or ""
            if card_message_id:
                hc_event = self._store.get_pending_hc_by_card(card_message_id)
        if hc_event is None:
            log.warning(
                "topic6 handle_card_action: 未找到 HC 记录 job=%s event=%s decision=%s",
                job_id, event_id, decision,
            )
            return
        if hc_event.resolved_at is not None:
            log.info(
                "topic6 handle_card_action: HC event=%s 已处理过(decision=%s),忽略重复回调",
                hc_event.id, hc_event.user_decision,
            )
            return

        job = self._store.get_job(hc_event.job_id)
        if job is None:
            log.warning("topic6 handle_card_action: 找不到 job=%s", hc_event.job_id)
            return

        note = _extract_action_note(action) or ""
        # 备注按钮:仅记录一条 remark 事件,不 resume——等用户 @bot 补一句正文再唤醒。
        if decision == DECISION_REMARK and not note:
            self._store.append_remark(hc_event.id, "等待补充说明")
            self._reply_sync(
                job.chat_id, f"📝 已记 {hc_kind} 备注意向,请再 @bot 一条说明。"
            )
            return

        self._store.resolve_hc_event(hc_event.id, decision, note)
        log.info(
            "topic6 hc resolved job=%s hc=%s event=%s decision=%s note=%s",
            job.job_id, hc_kind, hc_event.id, decision, note[:80],
        )

        if decision == DECISION_REJECT:
            self._store.mark_failed(
                job.job_id, f"{hc_kind} rejected: {note or 'no reason'}"[:400]
            )
            self._reply_sync(job.chat_id, f"❌ 已按 {hc_kind} 打回结束当前 pipeline。")
            return

        # pass / remark(带 note):把决策注入 MA Session 继续跑。
        decision_message = _build_decision_message(hc_kind, decision, note)
        try:
            await self._runner.resume_job(job, decision_message)
        except Exception as error:  # noqa: BLE001 - resume 失败也要通知飞书
            log.exception("topic6 resume_job failed job=%s: %s", job.job_id, error)
            self._reply_sync(job.chat_id, f"⚠️ {hc_kind} 续跑失败:{error!s}"[:200])

    # ---- helper --------------------------------------------------------------

    def _reply_sync(self, chat_id: str, text: str) -> None:
        try:
            self._runner._feishu.send_to_chat(chat_id, text)  # noqa: SLF001
        except Exception as error:  # noqa: BLE001
            log.warning("topic6 hitl reply failed chat=%s: %s", chat_id, error)


# ---- 纯函数:回调 payload 解析 ---------------------------------------------


def _extract_action_value(action: Any) -> Optional[dict]:
    """从 lark_channel CardActionEvent / dict 里挖出按钮 value 结构。"""
    inner = getattr(action, "action", None) or {}
    value = getattr(inner, "value", None) if inner else None
    if value is None and isinstance(action, dict):
        value = ((action.get("action") or {}).get("value"))
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return None
    if isinstance(value, dict):
        return value
    return None


def _extract_action_note(action: Any) -> Optional[str]:
    """备注按钮可能配合一个 input 组件;这里从 form_value / input_value 抽出说明文本。"""
    inner = getattr(action, "action", None)
    if inner is None:
        return None
    form_value = getattr(inner, "form_value", None) or {}
    if isinstance(form_value, dict):
        note = form_value.get("remark_note") or form_value.get("note")
        if isinstance(note, str) and note.strip():
            return note.strip()
    input_value = getattr(inner, "input_value", None)
    if isinstance(input_value, str) and input_value.strip():
        return input_value.strip()
    return None


def _build_decision_message(hc_kind: str, decision: str, note: str) -> str:
    """把决策拼成一句注入 MA Session 的 user.message。

    coordinator.system.md 的对应节点会解析这句 user.message:``HC1 pass`` 直接放行;
    ``HC1 reject: <原因>`` 触发回退;``HC1 remark: <补充>`` 视为带备注通过。
    """
    if decision == DECISION_PASS:
        return f"{hc_kind} pass"
    if decision == DECISION_REJECT:
        return f"{hc_kind} reject: {note or '无说明'}"
    if decision == DECISION_REMARK:
        return f"{hc_kind} remark: {note or '无补充'}"
    return f"{hc_kind} {decision}"
