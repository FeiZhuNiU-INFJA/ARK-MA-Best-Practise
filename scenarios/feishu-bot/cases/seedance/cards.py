"""Seedance 话题卡片：状态、需求、结果与产物集中展示。"""
from __future__ import annotations

import json
import re
import time


PHASES = {
    "waiting": ("等待创作", "grey", "grey-50"),
    "running": ("正在创作", "blue", "blue-50"),
    "delivering": ("正在回传产物", "blue", "blue-50"),
    "done": ("创作完成", "green", "green-50"),
    "partial": ("创作待确认", "orange", "orange-50"),
    "timed_out": ("等待超时", "orange", "orange-50"),
    "failed": ("执行失败", "red", "red-50"),
}


def clipped(text: str, limit: int) -> str:
    # 截断模型正文时同时隐藏链接，避免截出半个签名 URL。
    if len(text) <= limit:
        return text
    plain = re.sub(r"!?\[([^\]]*)\]\([^)]+\)", r"\1", text)
    return plain[:limit] + "\n…完整内容请结合话题内产物查看。"


def markdown(text: str, size: str = "normal") -> dict:
    return {"tag": "markdown", "content": text, "text_size": size, "margin": "0px"}


def file_line(item: dict) -> str:
    name = item["name"].replace("[", "［").replace("]", "］")
    if item.get("url"):
        return f"• [{name}](<{item['url']}>) · 下载链接有时效，可用 /files 刷新"
    return f"• {name} · 已回传"


def build_card(state: dict, *, group: bool) -> dict:
    title, color, background = PHASES.get(state.get("phase"), PHASES["waiting"])
    elements = [{
        "tag": "column_set",
        "columns": [{
            "tag": "column", "width": "weighted", "weight": 1,
            "background_style": background, "padding": "12px",
            "vertical_spacing": "8px",
            "elements": [
                markdown(f"**{title}**", "heading-2"),
                markdown(clipped(state.get("detail") or "描述你的图片或视频需求。", 600)),
            ],
        }],
    }]
    if state.get("attached_session"):
        elements.append(markdown(f"**Session ID**：`{state['attached_session']}`", "notation"))
    if state.get("request"):
        elements.append(markdown("**创作需求**\n" + clipped(state["request"], 500)))
    if state.get("started_at"):
        updated = state.get("progress_at") or state["started_at"]
        elapsed = max(0, int((updated - state["started_at"]) / 1000))
        stage = state.get("stage") or "正在准备创作。"
        elements.append(markdown(
            f"**当前阶段**\n{stage}\n已用时 {elapsed // 60} 分 {elapsed % 60} 秒"
            f" · 更新于 {time.strftime('%H:%M:%S', time.localtime(updated / 1000))}",
            "notation",
        ))
    if state.get("result"):
        elements.append(markdown("**创作结果**\n" + clipped(state["result"], 2400)))
    files = state.get("files", [])
    if files:
        lines = []
        # 文件链接完整保留；按整行预算裁剪，给其余卡片内容留出空间。
        for item in files[:8]:
            candidate = file_line(item)
            if len(json.dumps(lines + [candidate], ensure_ascii=False).encode()) > 12000:
                break
            lines.append(candidate)
        if len(lines) < len(files):
            lines.append("• 其余产物可用 /files 重新获取。")
        elements.append(markdown("**交付文件**\n" + "\n".join(lines)))
    footer = clipped(state.get("notice") or "", 900)
    hint = (
        "在本话题直接发送修改要求或参考素材即可继续，无需再次 @Bot。"
        if group else "直接发送修改要求或参考素材即可继续创作。"
    )
    if state.get("phase") == "timed_out":
        hint = "将继续跟踪云端进度；/status 查看状态、/files 获取最终交付物，无需重复提交创作需求。"
    elements.append(markdown(
        (footer + "\n\n" if footer else "") + hint
        + "\n/status 查看状态 · /files 获取最终交付物 · /new 新创作 · /help 帮助",
        "notation",
    ))
    card = {
        "schema": "2.0",
        "config": {"width_mode": "default", "update_multi": True},
        "header": {"template": color, "title": {"tag": "plain_text", "content": "Seedance · 视觉创作"}},
        "body": {"direction": "vertical", "padding": "12px", "vertical_spacing": "16px", "elements": elements},
    }
    # PATCH 上限 30 KB；有界正文 + 有界完整链接留出足够余量。
    if len(json.dumps(card, ensure_ascii=False).encode()) > 28000:
        raise ValueError("创作卡片超出大小上限")
    return card
