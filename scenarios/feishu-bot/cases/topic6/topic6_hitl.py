"""topic6 HITL 卡片与回调:HC1 / HC2 / HC3 三种审核卡片的构造与按钮回调处理。

配合 :mod:`topic6_runner` 用:

  - Runner 在 Session 进入 idle 且末尾消息带 ``{"hc":"HCx", ...}`` 时,调
    ``Topic6Hitl.send_hc_card`` 把审核卡片发到当前 job 的 chat_id 里,并把返回的
    飞书 ``message_id`` 记到 ``pipeline_hc_events.card_message_id``。
  - 用户点卡片按钮后,飞书 SDK 通过 ``Events.CARD_ACTION`` 触发 gateway 侧回调;
    orchestrator 会把 payload 转交给 :meth:`Topic6Hitl.handle_card_action`。这里
    解出 ``action.value`` 里携带的 job_id / event_id / decision,反查 pipeline_hc_events,
    patch 卡片到"已处理"终态(按钮消失,显示决策 + 操作人),再调 runner.resume_job
    让 MA Session 继续跑。

卡片模板:统一用飞书 schema=2.0 交互卡片。三个 HC 的头部颜色区分:HC1 蓝、HC2 绿、
HC3 紫。按钮固定「通过 / 打回 / 备注」三个;备注按钮走 input 交互(飞书原生 form 提交
的 form_value 里带 remark_note)。已处理的卡片头部改灰色(wathet),按钮换成静态标语。
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

from pipeline_store import HcEvent, PipelineJob, PipelineStore
from topic6_runner import Topic6CardSenderProtocol, Topic6Runner

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
        "subtitle": "test 模式 500 条 / demo 模式 50 条,需要人工确认标注结果",
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
    if payload.get("__fallback__"):
        # 兜底 payload:Agent 违约没输出真正的 HC JSON,gateway 用文本意图检测把
        # 卡片补出来了。**不**把 agent 最后几百字尾巴附上——那段大概率是乱码 JSON 残片
        # 或 next_step_if_passed 之类的元描述,直接暴露给审核人会误导判断
        # (曾经出现"HC2 卡片正文却写 HC1"的诡异态,就是尾部截取里混入了 HC1 后续 step)。
        # 只留一行中性提示,让审核人回头翻上方 Coordinator 历史消息核实。
        elements.append(
            {
                "tag": "markdown",
                "content": (
                    "⚠️ AI 未按契约输出结构化载荷,以下摘要可能不完整;"
                    "请回滚历史消息核对实际指标后再决策。"
                ),
            }
        )
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


def build_hc_resolved_card(
    job: PipelineJob,
    hc_kind: str,
    payload: dict,
    decision: str,
    note: str,
    operator_label: str,
) -> dict:
    """构造"已处理"终态卡片:头部改灰(wathet),按钮换成决策标语,不再有可点行为。

    patch 用,替换原来那张带按钮的卡片。为了让用户能一眼看到"谁点的、点了啥",标题
    追加 emoji + 决策文本,正文追加一条 markdown 元数据行。原本的 payload 摘要保留,
    方便追溯当时看到的数据。
    """
    meta = _HC_META.get(hc_kind, _HC_META["HC1"])
    decision_emoji = {
        DECISION_PASS: "✅",
        DECISION_REJECT: "❌",
        DECISION_REMARK: "📝",
    }.get(decision, "•")
    decision_label = {
        DECISION_PASS: "通过",
        DECISION_REJECT: "打回",
        DECISION_REMARK: "备注",
    }.get(decision, decision)
    resolved_at = time.strftime("%Y-%m-%d %H:%M:%S")
    elements: list[dict] = [
        {
            "tag": "markdown",
            "content": (
                f"任务 ID:`{job.job_id}`  ·  运行模式:**{job.mode}**  ·  "
                f"当前阶段:**{job.current_phase}**"
            ),
        },
    ]
    if payload.get("__fallback__"):
        # 兜底 payload 的终态版:同 build_hc_card 里的处理,只留中性提示,不暴露尾部原文。
        elements.append(
            {
                "tag": "markdown",
                "content": (
                    "⚠️ AI 未按契约输出结构化载荷,摘要可能不完整;"
                    "决策已依据审核人回滚历史消息后的判断落库。"
                ),
            }
        )
    elements.extend(_payload_summary_elements(payload))
    elements.append({"tag": "hr"})
    resolved_line = f"{decision_emoji} **{decision_label}** · {operator_label} · {resolved_at}"
    if note:
        resolved_line += f"\n\n> {note}"
    elements.append({"tag": "markdown", "content": resolved_line})

    return {
        "schema": "2.0",
        "config": {"width_mode": "default"},
        "header": {
            "title": {
                "tag": "plain_text",
                "content": f"{meta['title']} · {decision_emoji} 已{decision_label}",
            },
            "subtitle": {"tag": "plain_text", "content": meta["subtitle"]},
            # 已处理卡片统一改灰蓝(wathet),头颜色的变化是最直观的"点过了"信号。
            "template": "wathet",
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

        ``action`` 是 lark_oapi 归一化后的 ``Card`` 对象,主要用到:
          - ``action.action.value``:我们塞进去的 dict(job_id/hc/event_id/decision)
          - ``action.open_message_id``:卡片 message_id(反查 + patch 用)
          - ``action.open_id``:点按钮的人;做归属校验 / 展示用

        点按钮后必须做三件事,缺一不可:
          1. store.resolve_hc_event 落库(审计)
          2. patch 卡片到"已处理"终态(视觉反馈,否则用户以为点了没生效)
          3. 若通过 / 备注 → runner.resume_job 把决策塞回 MA Session 继续跑
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
        # lark_oapi Card 对象把卡片消息 id 字段名叫 open_message_id,不是 message_id。
        card_message_id = (
            getattr(action, "open_message_id", "")
            or getattr(action, "message_id", "")
            or ""
        )
        hc_event: Optional[HcEvent] = None
        if isinstance(event_id, int) or (isinstance(event_id, str) and event_id.isdigit()):
            hc_event = self._store.get_hc_event(int(event_id))
        if hc_event is None and card_message_id:
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
            # 已处理但按钮还在?兜底再 patch 一次到终态,避免用户反复点。
            if card_message_id or hc_event.card_message_id:
                await self._patch_resolved_card(
                    card_message_id or hc_event.card_message_id,
                    hc_event,
                    action,
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

        # 视觉反馈:把原卡片 patch 成"已处理"终态。写库之后再 patch,顺序不能反,
        # 否则中途异常会留下"库里已 resolve 但卡片还挂着按钮"的诡异态。patch 失败
        # 不阻塞主流程——决策已经落库,resume 该跑还是跑。
        target_message_id = card_message_id or hc_event.card_message_id
        if target_message_id:
            hc_event_updated = HcEvent(
                id=hc_event.id,
                job_id=hc_event.job_id,
                hc_kind=hc_event.hc_kind,
                payload=hc_event.payload,
                card_message_id=hc_event.card_message_id,
                user_decision=decision,
                user_note=note,
                created_at=hc_event.created_at,
                resolved_at=int(time.time() * 1000),
            )
            await self._patch_resolved_card(target_message_id, hc_event_updated, action)

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

    async def _patch_resolved_card(
        self, card_message_id: str, hc_event: HcEvent, action: Any
    ) -> None:
        """把 HC 卡片 patch 到"已处理"终态。失败降级为一条文本兜底,不抛。"""
        job = self._store.get_job(hc_event.job_id)
        if job is None:
            return
        import asyncio

        loop = asyncio.get_event_loop()
        operator_label = await loop.run_in_executor(
            None,
            lambda: _resolve_operator_label(
                action, self._runner._feishu  # noqa: SLF001
            ),
        )
        resolved_card = build_hc_resolved_card(
            job=job,
            hc_kind=hc_event.hc_kind,
            payload=hc_event.payload or {},
            decision=hc_event.user_decision,
            note=hc_event.user_note or "",
            operator_label=operator_label,
        )
        try:
            await loop.run_in_executor(
                None,
                lambda: self._runner._feishu.patch_interactive_card(  # noqa: SLF001
                    card_message_id, resolved_card
                ),
            )
        except Exception as error:  # noqa: BLE001 - patch 失败退化成一条文本
            log.warning(
                "topic6 patch resolved card failed job=%s hc=%s message=%s: %s",
                hc_event.job_id, hc_event.hc_kind, card_message_id, error,
            )

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


def _extract_operator_open_id(action: Any) -> tuple[str, str]:
    """从 lark_channel CardActionEvent / dict 里抠出 (open_id, chat_id)。

    lark_channel 归一后的 CardActionEvent 结构:顶层 chat_id / message_id,operator 子对象
    携带 open_id(见 lark_oapi.channel.types.EventOperator)。同时兼容纯 dict 形态,便于测试。
    open_id 为空时返回 ("", chat_id) 由上层兜底。
    """
    operator = getattr(action, "operator", None)
    if operator is None and isinstance(action, dict):
        operator = action.get("operator") or {}
    open_id = getattr(operator, "open_id", "") if operator is not None else ""
    if isinstance(operator, dict):
        open_id = open_id or str(operator.get("open_id") or "")
    chat_id = getattr(action, "chat_id", "") or ""
    if isinstance(action, dict):
        chat_id = chat_id or str(action.get("chat_id") or "")
    return str(open_id or ""), str(chat_id or "")


def _resolve_operator_label(action: Any, feishu: Any) -> str:
    """把卡片回调转成"已处理"卡片上的操作人标签,优先展示真名。

    飞书卡片按钮事件 payload 里只带 open_id(见 P2CardActionTrigger.operator),SDK 也没
    做姓名解析。走 feishu.resolve_operator_name 反查:群里命中 chat_roster 缓存零额外 API,
    单聊/名册 miss 单发 contact.v3.user.get。反查拿到真名就直接用;拿不到(权限没开 /
    contact API 失败)退回 open_id 后 6 位短标识;完全没 open_id 才落到"未知操作人"。
    """
    open_id, chat_id = _extract_operator_open_id(action)
    if not open_id:
        return "未知操作人"
    name = ""
    if feishu is not None:
        try:
            name = feishu.resolve_operator_name(open_id, chat_id=chat_id or None)
        except Exception:  # noqa: BLE001 - 反查失败退回 open_id 短标识
            name = ""
    if name:
        return name
    return f"操作人 ...{open_id[-6:]}"


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
