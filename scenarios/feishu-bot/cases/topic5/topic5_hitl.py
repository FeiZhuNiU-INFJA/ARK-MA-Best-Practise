"""topic5 HITL 卡片与回调:DAG 人工门(G1/G2/N10/G3/G4/HC7)的审核卡片构造与按钮回调。

⚠️ host-only:import ``arkagent`` 下游的 ``topic5_runner``,只能在 Gateway 宿主进程运行,
无法在 MA 云沙箱内独立 import 测试。

配合 :mod:`topic5_runner` 用:

  - Runner 在 Session 进入 idle 且末尾消息带 ``{"hc":"G3", ...}`` 时,调
    ``Topic5Hitl.send_hc_card`` 把任务主卡 patch 成审核卡,把复用的飞书 ``message_id``
    记到 ``pipeline_hc_events.card_message_id``。
  - 用户点卡片按钮后,飞书 SDK 通过 ``Events.CARD_ACTION`` 触发 gateway 侧回调;
    orchestrator 把 payload 转交 :meth:`Topic5Hitl.handle_card_action`,这里解出
    job_id / event_id / decision,反查 pipeline_hc_events,patch 卡片到"已处理"终态,
    再调 runner.resume_job 让 MA Session 继续跑。

决策注入格式严格遵守 ``HCx pass/reject/remark: note``(project_memory user_confirmed),
由薄 Coordinator 翻给 ``run_pipeline.py resume --hc {kind} --decision {...} --note {...}``。
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

try:
    from .pipeline_store import HcEvent, PipelineJob, PipelineStore, STATUS_WAIT_HC
    from .topic5_intake import (
        DEMO_REQUIREMENT_DEFAULTS,
        IntakeValidationError,
        build_requirement_form,
        format_requirement_snapshot,
        normalize_requirement,
        summarize_requirement,
    )
    from .topic5_runner import Topic5CardSenderProtocol, Topic5Runner
    from .topic5_progress_card import format_review_lines, format_label_snapshot, label_snapshot
except ImportError:  # pragma: no cover - standalone compatibility
    from pipeline_store import HcEvent, PipelineJob, PipelineStore, STATUS_WAIT_HC
    from topic5_intake import (
        DEMO_REQUIREMENT_DEFAULTS,
        IntakeValidationError,
        build_requirement_form,
        format_requirement_snapshot,
        normalize_requirement,
        summarize_requirement,
    )
    from topic5_runner import Topic5CardSenderProtocol, Topic5Runner
    from topic5_progress_card import format_review_lines, format_label_snapshot, label_snapshot

log = logging.getLogger("arkagent.topic5.hitl")

DECISION_PASS = "pass"
DECISION_REJECT = "reject"
DECISION_REMARK = "remark"
PENDING_REMARK_NOTE = "等待补充说明"

# 每个 HC 门的头部信息(颜色 + 图标 + 标题)。对齐 hc_bridge.NODE_TO_HC。
_HC_META = {
    "G1": {
        "template": "blue",
        "icon": "checkbox-checked_outlined",
        "title": "G1 · 需求确认",
        "subtitle": "确认复盘活动范围/口径,需要人工确认后进入方案",
    },
    "G2": {
        "template": "blue",
        "icon": "checkbox-checked_outlined",
        "title": "G2 · 方案确认",
        "subtitle": "指标口径/章节方案已就绪,需要人工确认后开跑",
    },
    "N10": {
        "template": "green",
        "icon": "check-square_outlined",
        "title": "N10 · 观点与 UGC 标签共同确认",
        "subtitle": "两套候选均确认后才进入第二轮标注",
    },
    "G3": {
        "template": "purple",
        "icon": "review_outlined",
        "title": "G3 · 报告质检终审",
        "subtitle": "报告已生成并经 Validator 扫描,请人工终审",
    },
    "G4": {
        "template": "purple",
        "icon": "review_outlined",
        "title": "G4 · 用户终验",
        "subtitle": "报告已交付飞书文档,请在文档内微调后确认",
    },
    "HC7": {
        "template": "red",
        "icon": "warning_outlined",
        "title": "HC7 · 异常升级",
        "subtitle": "流程遇到需要升级处理的异常,请人工裁决",
    },
    "USER_ESCALATE": {
        "template": "red",
        "icon": "warning_outlined",
        "title": "USER ESCALATE · 用户升级",
        "subtitle": "用户侧升级事项,请人工裁决",
    },
}


# ---- 卡片构造 --------------------------------------------------------------


def _fmt_payload_line(label: str, value: Any) -> Optional[dict]:
    if value in (None, "", [], {}):
        return None
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False)
        if len(text) > 300:
            text = text[:300] + "…"
        return {"tag": "markdown", "content": f"**{label}**\n```\n{text}\n```"}
    return {"tag": "markdown", "content": f"**{label}**:{value}"}


def _payload_summary_elements(payload: dict) -> list[dict]:
    """把 HC payload 的主要字段拍成一段可读摘要。字段挑选对齐引擎 HumanPause 产物。"""
    fields = [
        ("模式", payload.get("mode")),
        ("节点", payload.get("node")),
        ("说明", payload.get("prompt")),
        ("需提供", payload.get("expect")),
        ("项目目录", payload.get("project_dir")),
    ]
    elements: list[dict] = []
    for label, value in fields:
        node = _fmt_payload_line(label, value)
        if node:
            elements.append(node)
    return elements


_MODULE_LABELS = {
    "brand_context": "品牌背景",
    "activity_performance": "活动表现",
    "evidence_summary": "证据总结",
    "baseline_lift": "基准期对比",
    "product_funnel": "品牌/产品提及漏斗",
    "sentiment_opinion": "情感与观点",
    "kol_effectiveness": "KOL 层级表现",
    "ugc_participation": "UGC 参与",
    "wordcloud_topics": "平台词云议题",
    "top_content": "代表内容",
    "multi_activity_compare": "多活动对比",
}

_DATA_LABELS = {
    "activity_detail": "活动明细数据",
    "brand_period_and_baseline": "品牌活动期与基准期数据",
    "wordcloud": "平台词云数据",
}

_AUTHORITY_LABELS = {
    "user-uploaded-excel": "用户上传的活动明细文件",
    "controlled-stat-tool": "已登记的社媒统计能力",
}

_CHART_TYPE_LABELS = {
    "dual-axis line": "双轴趋势图",
    "donut": "环形图",
    "bar/funnel": "柱状图 / 漏斗图",
    "wordcloud/rank": "词云 / 高频词排行",
}

_CHART_CONDITION_LABELS = {
    "daily series available": "存在可用的逐日数据",
    "more than one platform": "覆盖两个及以上平台",
    "content_scope=all": "分析范围同时包含 PGC 与 UGC",
    "AI sentiment coverage complete": "AI 情感标注完整",
    "product query selects three-level funnel": "已提供产品关键词，可形成提及漏斗",
    "platform data available": "对应平台数据可用",
}

_PREFLIGHT_LABELS = {
    "required_requirement_fields": "需求信息完整",
    "activity_detail_reference": "活动明细文件已挂载",
    "registered_modules_only": "分析内容均有对应能力",
    "tool_routes_declared": "执行能力配置完整",
    "objectives_mapped_to_registered_modules": "业务目标均可被当前方案覆盖",
    "delivery_supported": "交付形式受支持",
    "allowed_actions_supported": "执行动作在授权范围内",
}

_REQUIREMENT_FIELD_LABELS = {
    "brand": "品牌",
    "activity_name": "活动名称",
    "period_start": "开始日期",
    "period_end": "结束日期",
    "platforms": "平台",
    "brand_query": "品牌关键词",
    "activity_query": "活动关键词",
    "content_scope": "内容范围",
    "business_questions": "业务问题",
    "delivery_expectation": "交付期望",
    "allowed_actions": "允许动作",
}


def _preflight_failure_detail(item: dict) -> str:
    name = str(item.get("name") or "")
    detail = item.get("detail")
    if name == "required_requirement_fields" and isinstance(detail, list):
        labels = [_REQUIREMENT_FIELD_LABELS.get(str(key), str(key)) for key in detail]
        return "缺少：" + "、".join(labels)
    if name == "activity_detail_reference":
        return "G2 确认后上传并挂载活动明细文件"
    if name == "objectives_mapped_to_registered_modules" and isinstance(detail, list):
        unsupported = [
            value for value in detail
            if isinstance(value, dict) and not value.get("supported")
        ]
        if unsupported:
            return "；".join(
                f"{value.get('objective') or '未命名目标'}"
                + (f"（{value.get('reason')}）" if value.get("reason") else "")
                for value in unsupported
            )
    if name in ("registered_modules_only", "tool_routes_declared"):
        return "内部执行配置不完整，请联系管理员处理"
    if name == "delivery_supported":
        return "当前交付形式不在已登记范围内"
    if name == "allowed_actions_supported":
        return "当前动作超出已授权范围"
    return str(detail or "需要调整方案")


def _plan_preflight(context: dict) -> dict:
    """Read preflight defensively from any G2 plan part.

    Coordinator transport may reorder a large review_context while preserving
    the three version-bound plan objects. The durable artifacts remain
    authoritative, so the card must not fail merely because preflight was
    forwarded under analysis_plan or selected_modules.
    """
    for key in ("execution_plan", "analysis_plan", "selected_modules"):
        part = context.get(key)
        if isinstance(part, dict) and isinstance(part.get("preflight"), dict):
            return part["preflight"]
    return {}


def _plan_review_elements(payload: dict) -> list[dict]:
    """把 G2 review_context 渲染为可直接审核的中文方案。"""
    context = payload.get("review_context")
    if not isinstance(context, dict):
        return []
    analysis = context.get("analysis_plan") or {}
    execution = context.get("execution_plan") or {}
    modules = context.get("selected_modules") or {}
    if not isinstance(analysis, dict) or not isinstance(execution, dict):
        return []

    scope = analysis.get("scope") or {}
    scope_name = {"all": "全部", "ugc": "仅 UGC", "pgc": "仅 PGC"}.get(
        str(scope.get("content_scope") or ""),
        str(scope.get("content_scope") or "未指定"),
    )
    scope_lines = [
        f"**品牌**：{scope.get('brand') or '未指定'}",
        f"**活动**：{'、'.join(str(x) for x in (scope.get('activities') or [])) or '未指定'}",
        (
            f"**周期**：{scope.get('period_start') or '未指定'} ～ "
            f"{scope.get('period_end') or '未指定'}"
        ),
        f"**平台**：{'、'.join(str(x) for x in (scope.get('platforms') or [])) or '未指定'}",
        f"**内容范围**：{scope_name}",
        f"**数据文件**：{scope.get('source_file_name') or 'G2 确认后上传'}",
    ]

    metric_lines = []
    for metric in analysis.get("metrics") or []:
        if not isinstance(metric, dict):
            continue
        suffix = ""
        if metric.get("name") == "NSR":
            suffix = "（分母含中性）" if metric.get("neutral_in_denominator") else "（分母不含中性）"
        metric_lines.append(
            f"- **{metric.get('name') or '指标'}**：{metric.get('definition') or '未说明'}{suffix}"
        )
    structure = " → ".join(str(x) for x in (analysis.get("report_structure") or []))

    stage_lines = []
    for index, stage in enumerate(execution.get("stages") or [], 1):
        if isinstance(stage, dict):
            stage_lines.append(
                f"{index}. **{stage.get('name') or '阶段'}**：{stage.get('work') or ''}"
            )
    gate_lines = [
        f"`{gate.get('gate')}` {gate.get('purpose')}"
        for gate in (execution.get("human_gates") or [])
        if isinstance(gate, dict)
    ]

    selected = modules.get("core_plus_conditional") or []
    module_lines = [
        f"- **{_MODULE_LABELS.get(str(name), str(name))}**"
        + (
            f"：{(modules.get('reasons') or {}).get(name)}"
            if (modules.get("reasons") or {}).get(name)
            else ""
        )
        for name in selected
    ]
    guardrail_lines = [
        f"- {item}" for item in (analysis.get("guardrails") or [])
    ]
    data_lines = [
        (
            f"- **{_DATA_LABELS.get(str(item.get('name')), str(item.get('name') or '数据'))}**"
            f"：{_AUTHORITY_LABELS.get(str(item.get('authority')), '按已确认方案获取')}"
            + (
                f"；校验 {item.get('validation')}"
                if item.get("validation") and item.get("name") != "activity_detail"
                else (
                    "；校验文件格式、字段、周期、活动分组和行数一致性"
                    if item.get("name") == "activity_detail" else ""
                )
            )
        )
        for item in (analysis.get("data_requirements") or [])
        if isinstance(item, dict)
    ]
    chart_lines = [
        (
            f"- **{item.get('section') or '章节'} / {item.get('chart') or '图表'}**"
            f"：{_CHART_TYPE_LABELS.get(str(item.get('type')), str(item.get('type') or '按数据选择'))}"
            f"；生成条件：{_CHART_CONDITION_LABELS.get(str(item.get('condition')), str(item.get('condition') or '始终'))}"
        )
        for item in (analysis.get("chart_plan") or [])
        if isinstance(item, dict)
    ]
    preflight = _plan_preflight(context)
    preflight_lines = [
        (
            f"- {'✅' if item.get('ok') else '⏳' if item.get('deferred') else '❌'} "
            f"**{_PREFLIGHT_LABELS.get(str(item.get('name')), '执行前检查')}**"
            + (
                ""
                if item.get("ok")
                else f"：{_preflight_failure_detail(item)}"
            )
        )
        for item in (preflight.get("checks") or [])
        if isinstance(item, dict)
    ]
    status_label = {
        "READY": "检查通过，可以开始执行",
        "READY_PENDING_INPUT": "方案可确认，确认后等待上传活动明细",
        "NEEDS_CAPABILITY_EXTENSION": "当前方案需要补充能力或调整目标",
        "BLOCKED": "信息不完整，暂不能开始执行",
    }.get(str(preflight.get("status")), "检查状态未知")

    return [
        {"tag": "markdown", "content": "**待确认方案 · 复盘范围**\n" + "\n".join(scope_lines)},
        {
            "tag": "markdown",
            "content": (
                "**核心指标与报告结构**\n"
                + ("\n".join(metric_lines) or "未生成指标口径")
                + f"\n\n**报告结构**：{structure or '未指定'}"
            ),
        },
        {
            "tag": "markdown",
            "content": (
                "**执行步骤**\n"
                + ("\n".join(stage_lines) or "未生成执行步骤")
                + "\n\n**人工确认点**："
                + (" · ".join(gate_lines) or "未指定")
                + f"\n\n**交付**：{execution.get('delivery') or '未指定'}"
            ),
        },
        {
            "tag": "markdown",
            "content": (
                "**启用模块**\n"
                + ("\n".join(module_lines) or "未登记模块")
                + "\n\n**口径护栏**\n"
                + ("\n".join(guardrail_lines) or "未登记护栏")
            ),
        },
        {
            "tag": "markdown",
            "content": (
                "**数据需求与证据要求**\n"
                + ("\n".join(data_lines) or "未登记数据需求")
                + "\n\n"
                + "\n".join(
                    f"- {item}" for item in (analysis.get("evidence_requirements") or [])
                )
            ),
        },
        {
            "tag": "markdown",
            "content": "**图表规划**\n" + ("\n".join(chart_lines) or "未登记图表规划"),
        },
        {
            "tag": "markdown",
            "content": (
                f"**执行前检查：{status_label}**\n"
                + ("\n".join(preflight_lines) or "未登记预检")
                + (
                    f"\n\n确认后将冻结方案版本 v{analysis.get('revision')}；"
                    "后续如调整范围、指标或模块，需要重新确认。"
                    if analysis.get("revision") else ""
                )
            ),
        },
    ]


def _label_review_elements(payload: dict) -> list[dict]:
    context = payload.get("review_context") or {}
    opinions = (context.get("opinion_candidates") or {}).get("candidates") or []
    ugc = (context.get("ugc_category_list") or {}).get("categories") or []
    refs = context.get("artifact_refs") or {}
    return [{
        "tag": "markdown",
        "content": (
            "**观点候选**\n"
            + ("\n".join(f"- {item}" for item in opinions) or "无适用观点候选")
            + "\n\n**UGC 参与方式候选**\n"
            + ("\n".join(f"- {item}" for item in ugc) or "无适用 UGC 类别")
            + "\n\n**候选版本**\n"
            + "\n".join(
                f"- `{key}`：`{(ref or {}).get('artifact_id')}`"
                for key, ref in refs.items()
            )
        ),
    }]


def _qc_review_elements(payload: dict) -> list[dict]:
    context = payload.get("review_context") or {}
    qc = context.get("qc_report")
    if not isinstance(qc, dict):
        return [{
            "tag": "markdown",
            "content": "❌ **缺少独立 Validator 结果，当前关卡不可通过。**",
        }]
    issues = qc.get("issues") or []
    issue_lines = [
        (
            f"- **{item.get('severity', 'UNKNOWN')} · {item.get('title', '未命名问题')}**"
            f"\n  {item.get('evidence') or item.get('msg') or '无证据说明'}"
        )
        for item in issues if isinstance(item, dict)
    ]
    return [{
        "tag": "markdown",
        "content": (
            f"**独立 Validator 结果** · `P0={qc.get('p0_count', 0)}`"
            f" · `P1={qc.get('p1_count', 0)}` · `P2={qc.get('p2_count', 0)}`"
            f"\n\nValidation：`{qc.get('validation_id') or '缺失'}`"
            f"\n规则版本：`{qc.get('rules_version') or '缺失'}`"
            f"\n被检对象：`{((qc.get('subject') or {}).get('artifact_id')) or '缺失'}`"
            + "\n\n"
            + ("\n".join(issue_lines) or "未发现问题")
        ),
    }]


def _delivery_review_elements(payload: dict) -> list[dict]:
    context = payload.get("review_context") or {}
    draft = context.get("delivery_draft") or {}
    return [{
        "tag": "markdown",
        "content": (
            "**待终验飞书文档**\n"
            f"- 链接：{draft.get('url') or '缺失'}\n"
            f"- 文档 revision：`{draft.get('revision_id') or '缺失'}`\n"
            f"- 报告版本：`{draft.get('report_version') or '缺失'}`\n"
            f"- 回读状态：`{draft.get('state') or 'unknown'}`\n"
            f"- Report Artifact：`{((context.get('report_ref') or {}).get('artifact_id')) or '缺失'}`\n"
            f"- Delivery Artifact：`{((context.get('delivery_ref') or {}).get('artifact_id')) or '缺失'}`"
        ),
    }]


def _decision_block_reason(hc_kind: str, payload: dict, decision: str) -> str:
    """Reject a pass action when the reviewed object is incomplete or not passable."""
    if decision != DECISION_PASS:
        return ""
    context = payload.get("review_context") or {}
    if hc_kind == "G2":
        execution = context.get("execution_plan") or {}
        if _plan_preflight(context).get("status") not in (
            "READY",
            "READY_PENDING_INPUT",
        ):
            return "方案生产预检未通过，不能确认进入生产 Run"
        plan_parts = [
            context.get("analysis_plan"),
            context.get("execution_plan"),
            context.get("selected_modules"),
        ]
        bundle_hashes = {
            part.get("plan_bundle_sha256")
            for part in plan_parts
            if isinstance(part, dict)
        }
        if (
            any(not isinstance(part, dict) for part in plan_parts)
            or None in bundle_hashes
            or len(bundle_hashes) != 1
        ):
            return "方案对象不完整或版本哈希不一致，不能确认"
    elif hc_kind == "N10":
        if not context.get("opinion_candidates") or not context.get("ugc_category_list"):
            return "观点与 UGC 两套候选未齐，不能确认"
    elif hc_kind == "G3":
        qc = context.get("qc_report")
        if not isinstance(qc, dict) or qc.get("validator") != "topic5-validator":
            return "缺少独立 Validator 结果，不能通过 G3"
        if qc.get("has_p0") or int(qc.get("p0_count") or 0) > 0:
            return "Validator 仍有 P0，必须先返修"
        if not qc.get("subject"):
            return "Validator 结果未绑定明确报告版本，不能通过"
    elif hc_kind == "G4":
        draft = context.get("delivery_draft") or {}
        if draft.get("state") != "ready" or not draft.get("revision_id"):
            return "交付草稿未通过回读检查或未绑定 revision，不能终验通过"
    return ""


def _action_value(
    job_id: str,
    hc_kind: str,
    event_id: int,
    decision: str,
    *,
    form_revision: str = "",
) -> dict:
    value = {
        "scenario": "topic5",
        "job_id": job_id,
        "hc": hc_kind,
        "event_id": event_id,
        "decision": decision,
    }
    if form_revision:
        value["form_revision"] = form_revision
    return value


def build_hc_card(
    job: PipelineJob,
    hc_kind: str,
    event_id: int,
    payload: dict,
    review_lines: tuple[str, ...] = (),
) -> dict:
    """构造一张 HC 交互卡片(schema=2.0)。各门共用骨架,靠 _HC_META 区分。"""
    meta = _HC_META.get(hc_kind, _HC_META["G1"])
    elements: list[dict] = [
        {
            "tag": "markdown",
            "content": (
                f"任务 ID:`{job.job_id}`  ·  运行模式:**{job.mode}**  ·  "
                f"当前关卡:**{hc_kind}**"
            ),
        },
    ]
    if review_lines:
        elements.extend(
            [
                {
                    "tag": "markdown",
                    "content": (
                        "**已完成审核**\n"
                        + "\n".join(f"- {line}" for line in review_lines)
                    ),
                },
                {"tag": "hr"},
            ]
        )
    if payload.get("__fallback__"):
        elements.append(
            {
                "tag": "markdown",
                "content": (
                    "⚠️ AI 未按契约输出结构化载荷,以下摘要可能不完整;"
                    "请回滚历史消息核对实际内容后再决策。"
                ),
            }
        )
    elements.extend(_payload_summary_elements(payload))
    if hc_kind == "G2":
        elements.extend(_plan_review_elements(payload))
    elif hc_kind == "N10":
        elements.extend(_label_review_elements(payload))
    elif hc_kind == "G3":
        elements.extend(_qc_review_elements(payload))
    elif hc_kind == "G4":
        elements.extend(_delivery_review_elements(payload))
    elements.append({"tag": "hr"})
    if hc_kind == "G1":
        elements.append(
            {
                "tag": "markdown",
                "content": (
                    "**需求确认**\n"
                    "请填写并提交下面的需求问卷。需求确认后将编制分析方案。"
                ),
            }
        )
        elements.append(
            build_requirement_form(
                event_id,
                lambda decision: _action_value(
                    job.job_id,
                    hc_kind,
                    event_id,
                    decision,
                    form_revision="requirement",
                ),
                defaults=DEMO_REQUIREMENT_DEFAULTS if job.mode == "demo" else None,
            )
        )
    else:
        elements.append(
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
        )
    if hc_kind != "G1":
        elements.append(
            {
                "tag": "form",
                "name": f"reject_form_{event_id}",
                "elements": [
                    {
                        "tag": "input",
                        "name": "reject_reason",
                        "required": True,
                        "input_type": "multiline_text",
                        "rows": 2,
                        "auto_resize": True,
                        "max_rows": 4,
                        "max_length": 500,
                        "width": "fill",
                        "label": {"tag": "plain_text", "content": "打回原因"},
                        "placeholder": {
                            "tag": "plain_text",
                            "content": "说明需要回退或调整的内容,提交后由 Agent 继续处理",
                        },
                    },
                    {
                        "tag": "button",
                        "name": "reject_submit",
                        "text": {"tag": "plain_text", "content": "打回并继续"},
                        "type": "danger",
                        "width": "fill",
                        "action_type": "form_submit",
                        "value": _action_value(
                            job.job_id,
                            hc_kind,
                            event_id,
                            DECISION_REJECT,
                        ),
                    },
                ],
            }
        )
    footer = (
        "> 提交问卷后将冻结需求快照并进入方案编制"
        if hc_kind == "G1"
        else "> 通过:进入下一阶段    ·  打回并继续:Agent 根据原因回退或调整后重试"
    )
    elements.append({"tag": "markdown", "content": footer})
    if job.task_id:
        elements.extend(
            [
                {"tag": "hr"},
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "停止任务"},
                    "type": "danger",
                    "width": "fill",
                    "confirm": {
                        "title": {"tag": "plain_text", "content": "确认停止任务"},
                        "text": {
                            "tag": "plain_text",
                            "content": "停止后当前审核按钮会失效。",
                        },
                    },
                    "behaviors": [
                        {
                            "type": "callback",
                            "value": {
                                "action": "stop_task",
                                "scenario": "topic5",
                                "job_id": job.job_id,
                                "card_version": 1,
                            },
                        }
                    ],
                },
            ]
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
    job: PipelineJob, hc_kind: str, payload: dict, decision: str, note: str, operator_label: str,
) -> dict:
    """构造"已处理"终态卡片:头部改灰(wathet),按钮换成决策标语,不再有可点行为。"""
    meta = _HC_META.get(hc_kind, _HC_META["G1"])
    decision_emoji = {DECISION_PASS: "✅", DECISION_REJECT: "❌", DECISION_REMARK: "📝"}.get(decision, "•")
    decision_label = {DECISION_PASS: "通过", DECISION_REJECT: "打回", DECISION_REMARK: "备注"}.get(decision, decision)
    resolved_at = time.strftime("%Y-%m-%d %H:%M:%S")
    elements: list[dict] = [
        {
            "tag": "markdown",
            "content": (
                f"任务 ID:`{job.job_id}`  ·  运行模式:**{job.mode}**  ·  "
                f"当前关卡:**{hc_kind}**"
            ),
        },
    ]
    if payload.get("__fallback__"):
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
    if hc_kind == "G2":
        elements.extend(_plan_review_elements(payload))
    elif hc_kind == "N10":
        if decision == DECISION_PASS:
            elements.append({
                "tag": "markdown",
                "content": format_label_snapshot(payload, note),
            })
        else:
            elements.extend(_label_review_elements(payload))
    elif hc_kind == "G3":
        elements.extend(_qc_review_elements(payload))
    elif hc_kind == "G4":
        elements.extend(_delivery_review_elements(payload))
    elements.append({"tag": "hr"})
    resolved_line = f"{decision_emoji} **{decision_label}** · {operator_label} · {resolved_at}"
    requirement_snapshot: Optional[dict] = None
    if note:
        display_note = note
        if hc_kind == "N10" and decision == DECISION_PASS:
            display_note = "观点与 UGC 标签已确认"
        if hc_kind == "G1" and decision == DECISION_PASS:
            try:
                loaded = json.loads(note)
                if isinstance(loaded, dict):
                    requirement_snapshot = loaded
                    display_note = summarize_requirement(requirement_snapshot)
                else:
                    display_note = "需求问卷已确认"
            except (TypeError, ValueError):
                display_note = "需求问卷已确认"
        resolved_line += f"\n\n> {display_note}"
    elements.append({"tag": "markdown", "content": resolved_line})
    if requirement_snapshot:
        elements.append(
            {
                "tag": "markdown",
                "content": format_requirement_snapshot(requirement_snapshot),
            }
        )

    return {
        "schema": "2.0",
        "config": {"width_mode": "default"},
        "header": {
            "title": {
                "tag": "plain_text",
                "content": f"{meta['title']} · {decision_emoji} 已{decision_label}",
            },
            "subtitle": {"tag": "plain_text", "content": meta["subtitle"]},
            "template": "wathet",
            "icon": {"tag": "standard_icon", "token": meta["icon"]},
        },
        "body": {"elements": elements},
    }


# ---- HITL 主类 -------------------------------------------------------------


@dataclass
class Topic5HitlDeps:
    store: PipelineStore
    runner: Topic5Runner


class Topic5Hitl(Topic5CardSenderProtocol):
    """topic5 的 HITL 门面:发卡片 + 收回调。"""

    def __init__(self, deps: Topic5HitlDeps):
        self._store = deps.store
        self._runner = deps.runner

    async def process_due_g4_timeouts(self, now: Optional[int] = None) -> int:
        """Idempotently send the 2-day reminder and escalate after day 3."""
        handled = 0
        current = now or int(time.time())
        for event in self._store.list_due_g4_events(current):
            job = self._store.get_job(event.job_id)
            if (
                job is None
                or job.status != STATUS_WAIT_HC
                or job.current_phase != "G4"
            ):
                continue
            if event.reminded_at is None:
                if not self._store.mark_g4_reminded(event.id, current):
                    continue
                await self._runner.reply_for_job(
                    job,
                    "G4 终验已等待 2 个工作日，请确认当前飞书文档 revision，"
                    "或在审核卡中说明需修改内容。",
                )
                handled += 1
                continue
            if (
                event.escalate_at is not None
                and event.escalate_at <= current
                and event.escalated_at is None
            ):
                if not self._store.claim_hc_event(
                    event.id,
                    "escalate",
                    "G4 等待 2 个工作日催办后再 1 个工作日仍无响应",
                    operator_label="系统超时策略",
                ):
                    continue
                await self._runner.reply_for_job(
                    job,
                    "G4 终验已超过 3 个工作日，现升级给 Supervisor；"
                    "流程不会绕过用户终验自动提交。",
                )
                await self._runner.resume_job(
                    job,
                    "G4 escalate: 2个工作日催办后再1个工作日仍无响应",
                )
                handled += 1
                continue
        return handled

    # ---- SendHcCard(runner → hitl)----------------------------------------

    async def send_hc_card(
        self,
        _job: PipelineJob,
        _hc_kind: str,
        _event_id: int,
        _payload: dict,
    ) -> Optional[str]:
        if _hc_kind == "G3":
            log.error(
                "topic5 refused user-facing G3 card job=%s event=%s",
                _job.job_id,
                _event_id,
            )
            return None
        review_lines = tuple(
            format_review_lines(
                self._store.list_resolved_hc_for_job(_job.job_id)
            )
        )
        card = build_hc_card(
            _job,
            _hc_kind,
            _event_id,
            _payload,
            review_lines=review_lines,
        )
        try:
            message_id = await self._runner.patch_job_card(_job.job_id, card)
            if message_id is None:
                loop = asyncio.get_event_loop()
                if _job.root_message_id:
                    # 话题模式:没有可 patch 的进度卡时,把 HC 卡回复触发消息,落进同一话题。
                    message_id, thread_id = await loop.run_in_executor(
                        None,
                        lambda: self._runner._feishu.send_interactive_card_in_thread(  # noqa: SLF001
                            _job.root_message_id, card
                        ),
                    )
                    if thread_id and not _job.topic_thread_id:
                        self._store.set_topic_thread_id(_job.job_id, thread_id)
                        _job.topic_thread_id = thread_id
                else:
                    message_id = await loop.run_in_executor(
                        None,
                        lambda: self._runner._feishu.send_interactive_card(  # noqa: SLF001
                            _job.chat_id, card
                        ),
                    )
                if message_id:
                    self._store.set_progress_card_message_id(_job.job_id, message_id)
                    _job.progress_card_message_id = message_id
        except Exception as error:  # noqa: BLE001
            log.exception("topic5 send_hc_card failed job=%s hc=%s: %s", _job.job_id, _hc_kind, error)
            raise
        log.info("topic5 hc card shown job=%s hc=%s message_id=%s", _job.job_id, _hc_kind, message_id)
        return message_id

    # ---- HandleCardAction(orchestrator → hitl)----------------------------

    async def handle_card_action(self, action: Any) -> None:
        payload = _extract_action_value(action)
        if not payload:
            log.warning("topic5 handle_card_action: 无 value,忽略。raw=%r", action)
            return

        decision = str(payload.get("decision") or "").strip().lower()
        event_id = payload.get("event_id")
        job_id = payload.get("job_id") or ""
        hc_kind = payload.get("hc") or ""

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
                "topic5 handle_card_action: 未找到 HC 记录 job=%s event=%s decision=%s",
                job_id, event_id, decision,
            )
            return
        hc_kind = hc_event.hc_kind
        if hc_event.resolved_at is not None:
            log.info(
                "topic5 handle_card_action: HC event=%s 已处理过(decision=%s),忽略重复回调",
                hc_event.id, hc_event.user_decision,
            )
            if card_message_id or hc_event.card_message_id:
                await self._patch_resolved_card(
                    card_message_id or hc_event.card_message_id, hc_event, action
                )
            return

        job = self._store.get_job(hc_event.job_id)
        if job is None:
            log.warning("topic5 handle_card_action: 找不到 job=%s", hc_event.job_id)
            return
        latest = self._store.latest_hc_for_job(job.job_id, hc_kind)
        if (
            job.status != STATUS_WAIT_HC
            or job.current_phase != hc_kind
            or latest is None
            or latest.id != hc_event.id
        ):
            log.warning(
                "topic5 stale hc ignored job=%s event=%s latest=%s status=%s phase=%s",
                job.job_id,
                hc_event.id,
                latest.id if latest else None,
                job.status,
                job.current_phase,
            )
            await self._runner.reply_for_job(
                job, f"{hc_kind} 审核对象已更新，这张旧卡仅保留历史，未推进流程。"
            )
            return

        note = _extract_action_note(action) or ""
        decision_note = note
        audit_note = note
        requirement: Optional[dict] = None
        blocked = _decision_block_reason(
            hc_kind, hc_event.payload or {}, decision
        )
        if blocked:
            await self._runner.reply_for_job(job, blocked)
            return
        if hc_kind == "G1" and decision == DECISION_PASS:
            try:
                requirement = normalize_requirement(_extract_form_value(action))
            except IntakeValidationError as error:
                await self._runner.reply_for_job(
                    job,
                    f"需求问卷未通过校验：{error}",
                )
                return
            # 兼容切换前已在 G1 挂载文件的活跃 Session；新任务在 G1 没有该字段。
            if job.source_file_id:
                requirement["source_file"] = {
                    "name": job.source_file_name,
                    "mount_path": job.source_file_mount_path,
                }
            decision_note = await self._runner.attach_requirement_payload(
                job, requirement, hc_event.id
            )
            audit_note = json.dumps(
                requirement, ensure_ascii=False, separators=(",", ":")
            )
        elif hc_kind == "N10" and decision == DECISION_PASS:
            audit_note = json.dumps(
                label_snapshot(hc_event.payload, note),
                ensure_ascii=False, separators=(",", ":"),
            )
        # 备注按钮:仅记录一条 remark 事件,不 resume——等用户 @bot 补一句正文再唤醒。
        if decision == DECISION_REMARK and not audit_note:
            if not self._store.append_current_hc_remark(
                hc_event.id, PENDING_REMARK_NOTE
            ):
                await self._runner.reply_for_job(
                    job, f"{hc_kind} 审核对象已更新，这张旧卡未记录备注。"
                )
                return
            self._reply_sync(
                job.chat_id,
                f"📝 已记 {hc_kind} 备注意向,请再 @bot 一条说明。",
                root_message_id=job.root_message_id,
            )
            return

        operator_label = await asyncio.get_event_loop().run_in_executor(
            None, lambda: _resolve_operator_label(action, self._runner._feishu)  # noqa: SLF001
        )
        if not self._store.claim_hc_event(
            hc_event.id, decision, audit_note, operator_label=operator_label
        ):
            await self._runner.reply_for_job(
                job, f"{hc_kind} 已由另一操作处理，本次回调未推进流程。"
            )
            return
        log.info(
            "topic5 hc resolved job=%s hc=%s event=%s decision=%s note=%s",
            job.job_id,
            hc_kind,
            hc_event.id,
            decision,
            (
                summarize_requirement(requirement)
                if requirement is not None
                else audit_note[:80]
            ),
        )

        target_message_id = card_message_id or hc_event.card_message_id
        if target_message_id:
            hc_event_updated = HcEvent(
                id=hc_event.id,
                job_id=hc_event.job_id,
                hc_kind=hc_event.hc_kind,
                payload=hc_event.payload,
                card_message_id=hc_event.card_message_id,
                user_decision=decision,
                user_note=audit_note,
                operator_label=operator_label,
                created_at=hc_event.created_at,
                resolved_at=int(time.time()),
            )
            await self._patch_resolved_card(
                target_message_id, hc_event_updated, action, operator_label=operator_label
            )

        if hc_kind == "G2" and decision == DECISION_PASS:
            if not job.source_file_id:
                await self._runner.wait_for_activity_file(job)
                return
            decision_note = json.dumps(
                {
                    "source_file": {
                        "name": job.source_file_name,
                        "mount_path": job.source_file_mount_path,
                    }
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        decision_message = _build_decision_message(
            hc_kind, decision, decision_note
        )
        try:
            await self._runner.resume_job(job, decision_message)
        except Exception as error:  # noqa: BLE001
            log.exception("topic5 resume_job failed job=%s: %s", job.job_id, error)
            self._reply_sync(
                job.chat_id,
                f"⚠️ {hc_kind} 续跑失败:{error!s}"[:200],
                root_message_id=job.root_message_id,
            )

    async def handle_remark_message(
        self, *, chat_id: str, thread_id: str, user_open_id: str, text: str,
    ) -> bool:
        """把"备注按钮后补发的文本"接回待审核 Job;未命中返回 False。"""
        job = self._store.get_active_job_by_session_key(chat_id, thread_id, user_open_id)
        if job is None:
            return False
        hc_event = self._store.latest_hc_for_job(job.job_id, job.current_phase)
        if (
            hc_event is None
            or hc_event.resolved_at is not None
            or hc_event.user_note != PENDING_REMARK_NOTE
        ):
            return False

        note = text.strip()
        if not note:
            return False
        action = {"chat_id": chat_id, "operator": {"open_id": user_open_id}}
        operator_label = await asyncio.get_event_loop().run_in_executor(
            None, lambda: _resolve_operator_label(action, self._runner._feishu)  # noqa: SLF001
        )
        self._store.resolve_hc_event(hc_event.id, DECISION_REMARK, note, operator_label=operator_label)
        if hc_event.card_message_id:
            resolved = HcEvent(
                id=hc_event.id,
                job_id=hc_event.job_id,
                hc_kind=hc_event.hc_kind,
                payload=hc_event.payload,
                card_message_id=hc_event.card_message_id,
                user_decision=DECISION_REMARK,
                user_note=note,
                operator_label=operator_label,
                created_at=hc_event.created_at,
                resolved_at=int(time.time()),
            )
            await self._patch_resolved_card(
                hc_event.card_message_id, resolved, action, operator_label=operator_label
            )
        await self._runner.resume_job(
            job, _build_decision_message(hc_event.hc_kind, DECISION_REMARK, note)
        )
        return True

    # ---- helper --------------------------------------------------------------

    async def _patch_resolved_card(
        self, card_message_id: str, hc_event: HcEvent, action: Any, *, operator_label: str = "",
    ) -> None:
        job = self._store.get_job(hc_event.job_id)
        if job is None:
            return
        if not operator_label:
            operator_label = hc_event.operator_label
        if not operator_label:
            loop = asyncio.get_event_loop()
            operator_label = await loop.run_in_executor(
                None, lambda: _resolve_operator_label(action, self._runner._feishu)  # noqa: SLF001
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
            await self._runner.patch_job_card(hc_event.job_id, resolved_card)
        except Exception as error:  # noqa: BLE001
            log.warning(
                "topic5 patch resolved card failed job=%s hc=%s message=%s: %s",
                hc_event.job_id, hc_event.hc_kind, card_message_id, error,
            )

    def _reply_sync(self, chat_id: str, text: str, root_message_id: str = "") -> None:
        try:
            if root_message_id:
                # 话题模式:文本回执回复触发消息,落进同一话题。
                self._runner._feishu.reply_in_thread(root_message_id, text)  # noqa: SLF001
            else:
                self._runner._feishu.send_to_chat(chat_id, text)  # noqa: SLF001
        except Exception as error:  # noqa: BLE001
            log.warning("topic5 hitl reply failed chat=%s: %s", chat_id, error)


# ---- 纯函数:回调 payload 解析 ---------------------------------------------


def _extract_action_value(action: Any) -> Optional[dict]:
    inner = getattr(action, "action", None)
    if inner is None and isinstance(action, dict):
        inner = action.get("action")
    inner = inner or {}
    value = getattr(inner, "value", None)
    if value is None and isinstance(inner, dict):
        value = inner.get("value")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            value = None
    if isinstance(value, dict):
        return value
    name = getattr(inner, "name", None)
    if name is None and isinstance(inner, dict):
        name = inner.get("name")
    if name == "reject_submit":
        return {"decision": DECISION_REJECT}
    if name == "remark_submit":
        return {"decision": DECISION_REMARK}
    return None


def _extract_form_value(action: Any) -> dict:
    """读取 card.action.trigger 的完整表单值。"""
    inner = getattr(action, "action", None)
    if inner is None and isinstance(action, dict):
        inner = action.get("action")
    if inner is None:
        return {}
    form_value = getattr(inner, "form_value", None)
    if form_value is None and isinstance(inner, dict):
        form_value = inner.get("form_value")
    if isinstance(form_value, str):
        try:
            form_value = json.loads(form_value)
        except (TypeError, ValueError):
            form_value = {}
    return form_value if isinstance(form_value, dict) else {}


def _extract_action_note(action: Any) -> Optional[str]:
    """从卡片 action 的 form_value / input_value 抽取备注文本。"""
    inner = getattr(action, "action", None)
    if inner is None and isinstance(action, dict):
        inner = action.get("action")
    if inner is None:
        return None
    form_value = _extract_form_value(action)
    if isinstance(form_value, dict):
        note = (
            form_value.get("reject_reason")
            or form_value.get("remark_note")
            or form_value.get("note")
        )
        if isinstance(note, str) and note.strip():
            return note.strip()
    input_value = getattr(inner, "input_value", None)
    if input_value is None and isinstance(inner, dict):
        input_value = inner.get("input_value")
    if isinstance(input_value, str) and input_value.strip():
        return input_value.strip()
    return None


def _extract_operator_open_id(action: Any) -> tuple[str, str]:
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
    open_id, chat_id = _extract_operator_open_id(action)
    if not open_id:
        return "未知操作人"
    name = ""
    if feishu is not None:
        try:
            name = feishu.resolve_operator_name(open_id, chat_id=chat_id or None)
        except Exception:  # noqa: BLE001
            name = ""
    if name:
        return name
    return f"操作人 ...{open_id[-6:]}"


def _build_decision_message(hc_kind: str, decision: str, note: str) -> str:
    """把决策拼成一句注入 MA Session 的 user.message。

    格式严格遵守 ``HCx pass/reject/remark: note``(project_memory user_confirmed),
    薄 Coordinator 据此调 ``run_pipeline.py resume --hc {kind} --decision {...} --note {...}``。
    """
    if decision == DECISION_PASS:
        if hc_kind == "G1" and note:
            if note.startswith("@/mnt/session/uploads/"):
                return f"{hc_kind} pass: {note}"
            encoded = base64.urlsafe_b64encode(note.encode("utf-8")).decode("ascii")
            return f"{hc_kind} pass: b64:{encoded}"
        return f"{hc_kind} pass: {note}" if note else f"{hc_kind} pass"
    if decision == DECISION_REJECT:
        return f"{hc_kind} reject: {note or '无说明'}"
    if decision == DECISION_REMARK:
        return f"{hc_kind} remark: {note or '无补充'}"
    return f"{hc_kind} {decision}"
