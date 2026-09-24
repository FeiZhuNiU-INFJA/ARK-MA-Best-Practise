"""topic6 pipeline 运行进度卡片构造(飞书 interactive card schema 2.0)。

只处理"渲染"这一件事:根据 (job, status, phase, tool_lines, elapsed_sec, error, online_url)
组装一个可直接 send/patch 的 card dict。

生命周期:一次 pipeline 全程复用同一张卡片(patch 覆写):
  - running  ⇒ 🚀 header + tool_lines
  - wait_hc  ⇒ ⏸️ header,提示"请在下方 HCx 卡片操作"
  - done     ⇒ ✅ header,底部加"打开报告"链接(若有 online_url)
  - failed   ⇒ ❌ header,末尾附错误摘要
"""
from __future__ import annotations

from typing import Iterable, Optional

from .pipeline_store import (
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_RUNNING,
    STATUS_WAIT_HC,
    PipelineJob,
)

# 卡片最多渲染的近期 tool 行数;超出用 "+N 条更早" 提示,防止 element 超过飞书上限。
MAX_TOOL_LINES = 30


def _format_elapsed(seconds: int) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _header(status: str, phase: str) -> dict:
    """标题按状态选 emoji + 颜色。飞书 header template: blue/green/red/grey/turquoise。"""
    if status == STATUS_DONE:
        title, template = f"✅ Topic6 Pipeline · 已完成", "green"
    elif status == STATUS_FAILED:
        title, template = f"❌ Topic6 Pipeline · 已失败", "red"
    elif status == STATUS_WAIT_HC:
        title, template = f"⏸️ Topic6 Pipeline · 等待审核 {phase}", "turquoise"
    else:
        title, template = f"🚀 Topic6 Pipeline · Phase {phase}", "blue"
    return {"title": {"tag": "plain_text", "content": title}, "template": template}


def _meta_line(job: PipelineJob, elapsed_sec: int) -> str:
    parts = [
        f"job=`{job.job_id}`",
        f"mode=`{job.mode}`",
        f"phase=`{job.current_phase}`",
        f"已运行 {_format_elapsed(elapsed_sec)}",
    ]
    return " · ".join(parts)


def _tool_lines_block(tool_lines: list[str], overflow: int) -> Optional[dict]:
    """把 tool_lines(升序:老 → 新)渲染成一段 lark_md;空就返回 None。"""
    if not tool_lines and overflow <= 0:
        return None
    body_lines: list[str] = []
    if overflow > 0:
        body_lines.append(f"_(更早 {overflow} 条已省略)_")
    body_lines.extend(tool_lines)
    return {
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": "**最近工具调用**\n" + "\n".join(body_lines),
        },
    }


def build_progress_card(
    *,
    job: PipelineJob,
    status: str,
    elapsed_sec: int,
    tool_lines: Iterable[str],
    overflow: int = 0,
    error: str = "",
    online_url: str = "",
) -> dict:
    """组装一张进度卡片 dict。参数只吃"当前快照",不做状态推断。

    - ``tool_lines``: 升序的最近若干条 tool 行(格式建议 ``HH:MM:SS · name · desc``)。
    - ``overflow``: buffer 已淘汰但未展示的条数,>0 时卡片顶部提示。
    - ``error`` / ``online_url``: 仅终态生效。
    """
    tool_snapshot = list(tool_lines)[-MAX_TOOL_LINES:]
    elements: list[dict] = [
        {"tag": "div", "text": {"tag": "lark_md", "content": _meta_line(job, elapsed_sec)}},
        {"tag": "hr"},
    ]
    tool_block = _tool_lines_block(tool_snapshot, overflow)
    if tool_block is not None:
        elements.append(tool_block)

    if status == STATUS_WAIT_HC:
        elements.append(
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"👉 请在下方 **{job.current_phase}** 卡片进行审核(通过/打回/备注)。",
                },
            }
        )
    elif status == STATUS_FAILED and error:
        elements.append(
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": f"**错误摘要**\n{error[:400]}"},
            }
        )
    elif status == STATUS_DONE and online_url:
        elements.append(
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "打开报告"},
                        "type": "primary",
                        "url": online_url,
                    }
                ],
            }
        )

    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": _header(status, job.current_phase),
        "elements": elements,
    }


__all__ = ["build_progress_card", "MAX_TOOL_LINES", "STATUS_RUNNING"]
