"""topic6 pipeline 运行进度卡片构造(飞书 interactive card schema 2.0)。

只处理"渲染"这一件事:根据 (job, status, phase, tool_lines, elapsed_sec, error, online_url)
组装一个可直接 send/patch 的 card dict。

生命周期:一次 pipeline 全程复用同一张卡片(patch 覆写):
  - running  ⇒ 🚀 header + tool_lines
  - wait_hc  ⇒ ⏸️ header,在同一张卡片中展示 HCx 操作
  - done     ⇒ ✅ header,底部加"打开报告"链接(若有 online_url)
  - failed   ⇒ ❌ header,末尾附错误摘要
"""
from __future__ import annotations

from typing import Iterable, Optional

try:
    from .pipeline_store import (
        FAILURE_SCOPE_RECOVERABLE,
        STATUS_DONE,
        STATUS_FAILED,
        STATUS_RUNNING,
        STATUS_STOPPING,
        STATUS_STOPPED,
        STATUS_WAIT_HC,
        PipelineJob,
    )
except ImportError:  # pragma: no cover - standalone compatibility
    from pipeline_store import (
        FAILURE_SCOPE_RECOVERABLE,
        STATUS_DONE,
        STATUS_FAILED,
        STATUS_RUNNING,
        STATUS_STOPPING,
        STATUS_STOPPED,
        STATUS_WAIT_HC,
        PipelineJob,
    )

# 卡片最多渲染的近期 tool 行数;超出用 "+N 条更早" 提示,防止 element 超过飞书上限。
MAX_TOOL_LINES = 10


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
        title, template = f"✅ Topic6 Pipeline · 已完成", "green"
    elif status == STATUS_STOPPED:
        title, template = f"⏹️ Topic6 Pipeline · 已停止", "grey"
    elif status == STATUS_FAILED:
        if failure_scope in ("", FAILURE_SCOPE_RECOVERABLE):
            title, template = "⚠️ Topic6 Pipeline · 执行失败，可重试", "orange"
        else:
            title, template = "❌ Topic6 Pipeline · 已终止", "red"
    elif status == STATUS_WAIT_HC:
        title, template = f"⏸️ Topic6 Pipeline · 等待审核 {phase}", "turquoise"
    elif status == STATUS_STOPPING:
        title, template = "Topic6 Pipeline · 正在停止", "grey"
    else:
        title, template = "🚀 Topic6 Pipeline · 运行中", "blue"
    return {"title": {"tag": "plain_text", "content": title}, "template": template}


PHASE_LABELS = {
    "A": "获取热点数据",
    "B": "标准化热点数据",
    "C": "标注热点内容",
    "C0": "标注基础事实",
    "C1": "标注热点内容",
    "C2": "分析热点内容",
    "C3": "标注事件节点",
    "D": "合并标注结果",
    "HC1": "小样本验收",
    "FULL": "全量数据标注",
    "HC2": "全量数据验收",
    "E": "分析热点与趋势",
    "F": "生成飞书周报",
    "HC3": "周报审核",
    "G": "发布报告网页",
    "H": "发布到妙搭",
}


def _step_summary(job: PipelineJob, status: str, elapsed_sec: int) -> str:
    phase = job.current_phase
    step = PHASE_LABELS.get(phase, phase)
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
    - ``review_lines``: 已完成 HC 的审核人、结果和时间，最多三条。
    - ``overflow``: buffer 已淘汰但未展示的条数,>0 时卡片顶部提示。
    - ``error`` / ``online_url``: 仅终态生效。
    """
    tool_snapshot = list(tool_lines)[-MAX_TOOL_LINES:]
    elements: list[dict] = [
        {"tag": "markdown", "content": _step_summary(job, status, elapsed_sec)},
    ]
    elements.extend(review_history_elements(list(review_lines)[-3:]))
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
                    "（通过，或填写打回原因后让 Agent 回退处理）。"
                ),
            }
        )
    elif status == STATUS_STOPPING:
        elements.append({"tag": "markdown", "content": "正在中断业务 Session，请稍候。"})
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
        and job.failure_scope in ("", FAILURE_SCOPE_RECOVERABLE)
    ):
        retry_elements = [
            {
                "tag": "markdown",
                "content": (
                    "业务 Session 仍可用时，可从当前阶段继续；"
                    "既有产物和审核记录保持不变。"
                ),
            }
        ]
        if job.task_id:
            retry_elements.append(
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "继续当前任务"},
                    "type": "primary_filled",
                    "width": "fill",
                    "behaviors": [
                        {
                            "type": "callback",
                            "value": {
                                "action": "retry_task",
                                "scenario": "topic6",
                                "job_id": job.job_id,
                                "card_version": 1,
                            },
                        }
                    ],
                }
            )
        else:
            retry_elements.append(
                {
                    "tag": "markdown",
                    "content": "发送 `/continue` 继续当前任务。",
                }
            )
        elements.extend(
            retry_elements
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
    if status in (STATUS_RUNNING, STATUS_WAIT_HC) and job.task_id:
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
                            "content": "只中断当前业务任务，已产生的轨迹仍会保留。",
                        },
                    },
                    "behaviors": [
                        {
                            "type": "callback",
                            "value": {
                                "action": "stop_task",
                                "scenario": "topic6",
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


__all__ = ["build_progress_card", "MAX_TOOL_LINES", "STATUS_RUNNING"]
