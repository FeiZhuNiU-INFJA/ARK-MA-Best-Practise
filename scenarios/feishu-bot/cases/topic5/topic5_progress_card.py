"""topic5 pipeline 运行进度卡片构造(飞书 interactive card schema 2.0)。

只处理"渲染"这一件事:根据 (job, status, phase, tool_lines, elapsed_sec, error, online_url)
组装一个可直接 send/patch 的 card dict。

生命周期:一次 pipeline 全程复用同一张卡片(patch 覆写):
  - running  ⇒ 🚀 header + tool_lines
  - wait_hc  ⇒ ⏸️ header,在同一张卡片中展示 HCx 操作
  - wait_input ⇒ ⏸️ header,提示 G2 后上传活动明细
  - done     ⇒ ✅ header,底部加"打开报告"链接(若有 online_url,即飞书 docx URL)
  - failed   ⇒ ❌ header,末尾附错误摘要

纯渲染,无框架依赖,可在沙箱内独立 import 测试。
"""
from __future__ import annotations

import json
import re
import time
from typing import Iterable, Optional

try:
    from .pipeline_store import (
        FAILURE_SCOPE_RECOVERABLE,
        HcEvent,
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
    )
    from .topic5_intake import format_requirement_snapshot
except ImportError:  # pragma: no cover - standalone compatibility
    from pipeline_store import (
        FAILURE_SCOPE_RECOVERABLE,
        HcEvent,
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
    )
    from topic5_intake import format_requirement_snapshot

# 卡片最多渲染的近期 tool 行数;超出用 "+N 条更早" 提示,防止 element 超过飞书上限。
MAX_TOOL_LINES = 10
MAX_REVIEW_LINES = 7

_PLAN_MODULE_LABELS = {
    "brand_context": "品牌背景",
    "activity_performance": "活动表现",
    "evidence_summary": "证据总结",
    "baseline_lift": "基准期对比",
    "sentiment_opinion": "情感与观点",
    "wordcloud_topics": "平台词云议题",
    "top_content": "代表内容",
    "kol_effectiveness": "KOL 层级表现",
    "product_funnel": "品牌/产品提及漏斗",
    "ugc_participation": "UGC 参与",
    "multi_activity_compare": "多活动对比",
}


def _format_elapsed(seconds: int) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _header(status: str, phase: str, failure_scope: str = "") -> dict:
    """标题按状态选 emoji + 颜色。飞书 header template: blue/green/red/grey/turquoise。"""
    if status == STATUS_DONE:
        title, template = "✅ Topic5 复盘报告 · 已完成", "green"
    elif status == STATUS_STOPPED:
        title, template = "⏹️ Topic5 复盘报告 · 已停止", "grey"
    elif status == STATUS_PAUSED:
        title, template = "⏸️ Topic5 复盘报告 · 已暂停", "grey"
    elif status == STATUS_FAILED:
        if failure_scope in ("", FAILURE_SCOPE_RECOVERABLE):
            title, template = "⚠️ Topic5 复盘报告 · 执行失败，可重试", "orange"
        else:
            title, template = "❌ Topic5 复盘报告 · 已终止", "red"
    elif status == STATUS_WAIT_HC:
        title, template = f"⏸️ Topic5 复盘报告 · 等待审核 {phase}", "turquoise"
    elif status == STATUS_WAIT_INPUT:
        title, template = "⏸️ Topic5 复盘报告 · 等待活动明细", "turquoise"
    elif status == STATUS_STOPPING:
        title, template = "Topic5 复盘报告 · 正在暂停", "grey"
    else:
        title, template = "🚀 Topic5 复盘报告 · 运行中", "blue"
    return {"title": {"tag": "plain_text", "content": title}, "template": template}


PHASE_LABELS = {
    "intake": "梳理复盘需求",
    "n00_intake": "准备需求问卷",
    "G1": "需求确认",
    "n01_hc_requirement": "需求确认",
    "n02_plan": "编制分析方案",
    "G2": "方案确认",
    "n03_hc_plan": "方案确认",
    "n04_run_init": "校验活动数据",
    "n05_brand_pull": "获取品牌数据",
    "n06_brand_baseline": "获取品牌基准数据",
    "n07_clean": "清洗活动明细",
    "clean": "清洗活动明细",
    "n08_ugc_discovery": "识别 UGC 类别",
    "n09_annotate_r1": "第一轮内容标注",
    "n09b_opinion_candidates": "整理候选观点",
    "n10_hc_opinion": "确认观点标签",
    "N10": "确认观点标签",
    "n11_annotate_r2": "第二轮内容标注",
    "aggregate": "汇总统计数据",
    "n12_aggregate": "汇总统计数据",
    "n13_wordcloud_pull": "获取词云数据",
    "n14_charts": "生成分析图表",
    "n15_wordcloud_insight": "分析词云与议题",
    "n16_report": "撰写复盘报告",
    "n17_validate": "检查报告质量",
    "G3": "报告质检",
    "n18_qc_gate": "复核质检结果",
    "n19_fix": "修订报告",
    "n20_delivery_draft": "生成飞书报告",
    "n20_delivery_gate": "检查交付文档",
    "G4": "报告终验",
    "n21_hc_user": "报告终验",
    "n22_user_gate": "处理终验结果",
    "HC7": "处理审核异常",
    "USER_ESCALATE": "处理审核异常",
    "n23_delivery_commit": "确认报告交付",
    "n24_handoff": "完成报告交接",
    "B_": "数据获取",
}


def _step_summary(job: PipelineJob, status: str, elapsed_sec: int) -> str:
    phase = job.current_phase
    step = PHASE_LABELS.get(phase, phase)
    if "_" in phase and any("\u4e00" <= ch <= "\u9fff" for ch in phase):
        step = phase.split("_", 1)[1]
    if status == STATUS_WAIT_INPUT:
        step = "等待上传活动明细"
    if status == STATUS_DONE:
        step = "报告交付完成"
    return f"**当前步骤 · {step}**\n已运行 {_format_elapsed(elapsed_sec)}"


def _tool_lines_block(tool_lines: list[str], overflow: int, title: str) -> Optional[dict]:
    """按发生顺序展示工具调用，默认收起。"""
    if not tool_lines and overflow <= 0:
        return None
    body_lines: list[str] = []
    if overflow > 0:
        body_lines.append(f"_(更早 {overflow} 条已省略)_")
    body_lines.extend(tool_lines)
    return {
        "tag": "collapsible_panel",
        "element_id": "recent_tool_calls",
        "expanded": False,
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "icon": {"tag": "standard_icon", "token": "down-small-ccm_outlined"},
            "icon_position": "right",
            "icon_expanded_angle": -180,
        },
        "elements": [{"tag": "markdown", "content": "\n".join(body_lines)}],
    }


def review_history_elements(review_lines: Iterable[str], heading: str = "审核记录") -> list[dict]:
    stages: dict[str, list[str]] = {}
    for line in review_lines:
        stage = line.partition(" · ")[0]
        stages.setdefault(stage, []).append(line)
    if not stages:
        return []

    elements = [{"tag": "markdown", "content": f"**{heading}** · 点击阶段可展开或收起"}]
    for stage, records in stages.items():
        summary = records[-1].splitlines()[0].split(" · ", 2)
        title = " · ".join(summary[:2])
        if stage in PHASE_LABELS:
            title += f" · {PHASE_LABELS[stage]}"
        elements.append({
            "tag": "collapsible_panel",
            "expanded": False,
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "icon": {"tag": "standard_icon", "token": "down-small-ccm_outlined"},
                "icon_position": "right",
                "icon_expanded_angle": -180,
            },
            "elements": [{"tag": "markdown", "content": "\n\n".join(records)}],
        })
    return elements


def _review_snapshot(review_lines: Iterable[str]) -> list[str]:
    """保留最新 G1/G2/N10 确认快照，限制重复关卡导致的卡片体积。"""
    lines = list(review_lines)
    if len(lines) <= MAX_REVIEW_LINES:
        return lines
    pinned = {
        index
        for kind in ("G1", "G2", "N10")
        for index in [
            next(
                (i for i in range(len(lines) - 1, -1, -1)
                 if lines[i].startswith(f"{kind} ·")),
                -1,
            )
        ]
        if index >= 0
    }
    remaining = [index for index in range(len(lines)) if index not in pinned]
    selected = pinned | set(remaining[-(MAX_REVIEW_LINES - len(pinned)):])
    return [lines[index] for index in sorted(selected)]


def label_snapshot(payload: dict, note: str = "") -> dict:
    """Freeze the reviewed N10 versions and additions, matching hc_bridge."""
    context = payload.get("review_context") or {}
    opinions = context.get("opinion_candidates") or {}
    ugc = context.get("ugc_category_list") or {}
    return {
        "labels": list(opinions.get("candidates") or opinions.get("labels") or [])
        + [item.strip() for item in re.split(r"[,，、]", note) if item.strip()],
        "ugc_categories": list(ugc.get("categories") or []),
        "ugc_not_applicable": bool(ugc.get("not_applicable")),
        "opinion_review_items": list(opinions.get("review_items") or []),
        "ugc_review_items": list(ugc.get("review_items") or []),
        "review_schema_version": max(
            int(opinions.get("review_schema_version") or 0),
            int(ugc.get("review_schema_version") or 0),
        ),
        "subject": context.get("artifact_refs") or {},
        "note": note,
    }


def format_label_snapshot(payload: dict, note: str = "") -> str:
    try:
        snapshot = json.loads(note)
    except (TypeError, ValueError):
        snapshot = None
    if not isinstance(snapshot, dict) or "labels" not in snapshot:
        snapshot = label_snapshot(payload, note)
    lines = ["**已确认观点标签**"]
    lines.extend(f"- {item}" for item in snapshot["labels"])
    if not snapshot["labels"]:
        lines.append("- 无适用观点标签")
    lines.append("**已确认 UGC 类别**")
    lines.extend(f"- {item}" for item in snapshot.get("ugc_categories") or [])
    if not snapshot.get("ugc_categories"):
        lines.append("- 不适用" if snapshot.get("ugc_not_applicable") else "- 无适用类别")
    refs = snapshot.get("subject") or {}
    if refs:
        lines.append("**确认版本**")
        lines.extend(
            f"- {key}：`{(value or {}).get('artifact_id') or (value or {}).get('ref') or '未记录'}`"
            for key, value in refs.items()
        )
    if snapshot.get("note"):
        lines.append(f"**审核备注**：{snapshot['note']}")
    return "\n".join(lines)


def format_plan_snapshot(payload: dict) -> str:
    """Render the version-bound G2 plan as a compact persistent audit summary."""
    context = payload.get("review_context") or {}
    analysis = context.get("analysis_plan") or {}
    execution = context.get("execution_plan") or {}
    modules = context.get("selected_modules") or {}
    scope = analysis.get("scope") or {}
    activities = "、".join(str(item) for item in scope.get("activities") or [])
    platforms = "、".join(str(item) for item in scope.get("platforms") or [])
    content_scope = {
        "all": "全部",
        "ugc": "仅 UGC",
        "pgc": "仅 PGC",
    }.get(str(scope.get("content_scope") or ""), "未指定")
    report_structure = " → ".join(
        str(item) for item in analysis.get("report_structure") or []
    )
    stage_names = " → ".join(
        str(item.get("name"))
        for item in execution.get("stages") or []
        if isinstance(item, dict) and item.get("name")
    )
    selected = modules.get("core_plus_conditional") or []
    module_names = "、".join(
        _PLAN_MODULE_LABELS.get(str(item), str(item)) for item in selected
    )
    preflight = next(
        (
            part["preflight"]
            for part in (execution, analysis, modules)
            if isinstance(part, dict) and isinstance(part.get("preflight"), dict)
        ),
        {},
    )
    preflight_label = {
        "READY": "已通过",
        "READY_PENDING_INPUT": "方案通过，待上传活动明细",
    }.get(str(preflight.get("status") or ""), "未记录")
    revision = analysis.get("revision") or execution.get("revision") or "未记录"
    plan_hash = (
        analysis.get("plan_bundle_sha256")
        or execution.get("plan_bundle_sha256")
        or modules.get("plan_bundle_sha256")
        or ""
    )
    lines = [
        "**已确认方案**",
        (
            f"- **范围**：{scope.get('brand') or '未指定'} / "
            f"{activities or '未指定'} / "
            f"{scope.get('period_start') or '未指定'} ～ "
            f"{scope.get('period_end') or '未指定'} / "
            f"{platforms or '未指定'} / {content_scope}"
        ),
        f"- **报告结构**：{report_structure or '未记录'}",
        f"- **执行阶段**：{stage_names or '未记录'}",
        f"- **启用模块**：{module_names or '未记录'}",
        f"- **生产预检**：{preflight_label}",
        (
            f"- **方案版本**：v{revision}"
            + (f" · `{str(plan_hash)[:12]}`" if plan_hash else "")
        ),
    ]
    return "\n".join(lines)


def format_review_lines(events: Iterable[HcEvent]) -> list[str]:
    """把审核事件渲染为持续展示的审计记录，保留 G1/G2/N10 确认快照。"""
    decision_meta = {
        "pass": ("✅", "通过"),
        "reject": ("❌", "打回"),
        "remark": ("📝", "备注通过"),
    }
    lines: list[str] = []
    for event in events:
        if event.hc_kind == "G3":
            continue
        emoji, label = decision_meta.get(
            event.user_decision, ("•", event.user_decision or "已处理")
        )
        operator = event.operator_label or "审核人未记录"
        resolved_at = time.strftime(
            "%H:%M", time.localtime(event.resolved_at or event.created_at)
        )
        line = (
            f"{event.hc_kind} · {emoji} {label} · "
            f"{operator} · {resolved_at}"
        )
        if event.user_decision in {"reject", "remark"} and event.user_note:
            note = re.sub(r"\s+", " ", event.user_note).strip()
            if note:
                if len(note) > 300:
                    note = note[:297] + "..."
                line += f"\n  ↳ **原因**：{note}"
        elif event.hc_kind == "G1" and event.user_decision == "pass":
            try:
                requirement = json.loads(event.user_note)
            except (TypeError, ValueError):
                requirement = None
            if isinstance(requirement, dict):
                details = format_requirement_snapshot(
                    requirement, heading=False
                )
                line += "\n  " + details.replace("\n", "\n  ")
        elif event.hc_kind == "G2" and event.user_decision == "pass":
            details = format_plan_snapshot(event.payload)
            line += "\n  " + details.replace("\n", "\n  ")
        elif event.hc_kind == "N10" and event.user_decision == "pass":
            details = format_label_snapshot(event.payload, event.user_note)
            line += "\n  " + details.replace("\n", "\n  ")
        lines.append(line)
    return lines


def build_progress_card(
    *,
    job: PipelineJob,
    status: str,
    elapsed_sec: int,
    tool_lines: Iterable[str],
    review_lines: Iterable[str] = (),
    overflow: int = 0,
    error: str = "",
    online_url: str = "",
) -> dict:
    """组装一张进度卡片 dict。参数只吃"当前快照",不做状态推断。

    - ``tool_lines``: 升序的最近若干条 tool 行(格式建议 ``HH:MM:SS · name · desc``)。
    - ``review_lines``: 已完成 HC 的审核人、结果和时间；渲染时保留 G1 并限制为七条。
    - ``overflow``: buffer 已淘汰但未展示的条数,>0 时卡片顶部提示。
    - ``error`` / ``online_url``: 仅终态生效。
    """
    tool_snapshot = list(tool_lines)[-MAX_TOOL_LINES:]
    elements: list[dict] = [
        {"tag": "markdown", "content": _step_summary(job, status, elapsed_sec)},
    ]
    elements.extend(review_history_elements(_review_snapshot(review_lines)))
    elements.append({"tag": "hr"})
    detail_title = "执行明细"
    if status == STATUS_RUNNING:
        indicator = "●" if elapsed_sec % 2 == 0 else "◉"
        detail_title = f"{indicator} 执行中 · 执行明细"
    tool_block = _tool_lines_block(tool_snapshot, overflow, detail_title)
    if tool_block is not None:
        elements.append(tool_block)

    if status == STATUS_WAIT_HC:
        elements.append(
            {
                "tag": "markdown",
                "content": (
                    f"👉 请在当前卡片完成 **{job.current_phase}** 审核"
                    "(通过,或填写打回原因后让 Agent 回退处理)。"
                ),
            }
        )
    elif status == STATUS_WAIT_INPUT:
        elements.append(
            {
                "tag": "markdown",
                "content": (
                    "👉 G2 方案已确认。请在当前话题上传活动明细 `.xlsx` 文件，"
                    "挂载成功后将自动进入生产 Run。"
                ),
            }
        )
    elif status == STATUS_STOPPING:
        elements.append({"tag": "markdown", "content": "正在暂停业务 Session，请稍候。"})
    elif status == STATUS_PAUSED:
        elements.extend(
            [
                {
                    "tag": "markdown",
                    "content": "任务已暂停。点击继续，或在当前话题发送新消息即可恢复。",
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "继续任务"},
                    "type": "primary_filled",
                    "width": "fill",
                    "behaviors": [
                        {
                            "type": "callback",
                            "value": {
                                "action": "resume_task",
                                "scenario": "topic5",
                                "job_id": job.job_id,
                                "card_version": 1,
                            },
                        }
                    ],
                },
            ]
        )
    elif status in (STATUS_FAILED, STATUS_STOPPED) and error:
        elements.append(
            {
                "tag": "markdown",
                "content": (
                    f"**停止原因**\n{error[:400]}"
                    if status == STATUS_STOPPED
                    else f"**错误摘要**\n{error[:400]}"
                ),
            }
        )
    if (
        status == STATUS_FAILED
        and job.task_id
        and job.failure_scope in ("", FAILURE_SCOPE_RECOVERABLE)
    ):
        elements.extend(
            [
                {
                    "tag": "markdown",
                    "content": (
                        (
                            "业务 Session 若仍为 `idle/running`，可从当前失败节点继续；"
                            "不会回到 G1，也不会重跑已通过的审核。"
                        )
                        if job.retry_strategy == RETRY_STRATEGY_NODE
                        else (
                            "业务 Session 若仍为 `idle/running`，可恢复当前任务；"
                            "既有产物和审核记录保持不变。"
                        )
                    ),
                },
                {
                    "tag": "button",
                    "text": {
                        "tag": "plain_text",
                        "content": (
                            "重试失败节点"
                            if job.retry_strategy == RETRY_STRATEGY_NODE
                            else "继续当前任务"
                        ),
                    },
                    "type": "primary_filled",
                    "width": "fill",
                    "behaviors": [
                        {
                            "type": "callback",
                            "value": {
                                "action": "retry_task",
                                "scenario": "topic5",
                                "job_id": job.job_id,
                                "card_version": 1,
                            },
                        }
                    ],
                },
            ]
        )
    elif status == STATUS_DONE and online_url:
        elements.append(
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "打开报告"},
                "type": "primary_filled",
                "width": "fill",
                "behaviors": [{"type": "open_url", "default_url": online_url}],
            }
        )
    if status in (STATUS_RUNNING, STATUS_WAIT_HC, STATUS_WAIT_INPUT) and job.task_id:
        elements.extend(
            [
                {"tag": "hr"},
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "暂停任务"},
                    "type": "danger",
                    "width": "fill",
                    "confirm": {
                        "title": {"tag": "plain_text", "content": "确认暂停任务"},
                        "text": {
                            "tag": "plain_text",
                            "content": "暂停后保留 Session、轨迹和产物，可随时继续。",
                        },
                    },
                    "behaviors": [
                        {
                            "type": "callback",
                            "value": {
                                "action": "pause_task",
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
        "header": _header(status, job.current_phase, job.failure_scope),
        "body": {"elements": elements},
    }


__all__ = [
    "build_progress_card",
    "format_review_lines",
    "MAX_TOOL_LINES",
    "STATUS_RUNNING",
]
