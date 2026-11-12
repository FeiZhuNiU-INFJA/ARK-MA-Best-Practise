"""回合级阶段与交付契约；Files API 没有沙箱路径，必须按声明匹配。"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import PurePosixPath
from urllib.parse import unquote

from arkagent.ark import event_text

TURN_CONTRACT = """本轮飞书交互要求：
开始实际执行及每次阶段切换时，先发一行 [stage] 阶段名称。
阶段名称使用：检索素材、脚本分镜、生成图片、生成视频、制作字幕、合成视频、质检、失败恢复。
长工具等待时保持真实状态，不编造百分比或预计完成时间。
仅在质检完成、确定可以交付时发布最终文件清单；部分失败时也只列实际可交付的文件。
最终回复包含：
[deliverables]
["/mnt/session/outputs/实际交付文件名.mp4"]
[/deliverables]
清单为 JSON 字符串数组，可以为空；只包含本轮最终交付物，不含检索图片、草稿、
中间帧、字幕调试文件或旧版本。用户明确要求交付的脚本/分镜可以列入。
文件必须真实存在。每次修改使用新文件名。随后用简洁中文和 Markdown 文件链接说明结果。
不要在进度回复中发布交付清单。不要显示内部命令、凭据或环境变量。
"""

STAGES = {
    "检索素材": "正在检索参考素材。",
    "脚本分镜": "正在编写脚本和分镜。",
    "生成图片": "正在生成图片。",
    "生成视频": "正在生成视频。",
    "制作字幕": "正在制作字幕。",
    "合成视频": "正在合成视频。",
    "质检": "正在检查成片质量。",
    "失败恢复": "正在修复问题并重试。",
}


def output_name(path: str) -> str | None:
    path = unquote(path.removeprefix("sandbox:"))
    if not path.startswith(("/mnt/session/outputs/", "/workspace/outputs/")):
        return None
    parts = PurePosixPath(path).parts
    if ".." in parts or path.endswith("/") or any(char in path for char in "\r\n\0"):
        return None
    return PurePosixPath(path).name


def deliverables(text: str, *, final: bool = False) -> set[str] | None:
    """None 表示未发布可信清单；旧回合只从最终回复的本地 Markdown 链接恢复。"""
    blocks = re.findall(r"\[deliverables\]\s*(.*?)\s*\[/deliverables\]", text, re.S)
    if "[deliverables]" in text:
        if not blocks:
            return None
        try:
            paths = json.loads(blocks[-1])
        except (ValueError, TypeError):
            return None
        if not isinstance(paths, list) or any(not isinstance(path, str) for path in paths):
            return None
        names = [output_name(path) for path in paths]
        return set(names) if all(names) else (set() if not paths else None)
    if final:
        names = {
            name for path in re.findall(r"!?\[[^\]]*\]\(<?([^)<>]+)>?\)", text)
            if (name := output_name(path))
        }
        return names or None
    return None


def clean_reply(text: str) -> str:
    text = re.sub(
        r"\[deliverables\].*?(?:\[/deliverables\]|$)", "", text, flags=re.S,
    )
    return re.sub(r"(?m)^\s*\[stage\][^\n]*\n?", "", text).strip()


def stage_label(text: str) -> str:
    """只输出固定概括，不把模型描述、路径或工具参数显示给用户。"""
    marker = re.search(r"\[stage\]\s*([^\n]+)", text)
    if marker:
        value = marker[1].strip()
        if value in STAGES:
            return STAGES[value]
    lowered = text.lower()
    rules = (
        (("未成功", "失败恢复", "修复", "重试"), "失败恢复"),
        (("质检", "响度", "规格检查", "质量检查", "quality check"), "质检"),
        (("合成", "重装配", "装配", "重新生成成片", "composevideo", "compose_video", "ffmpeg"), "合成视频"),
        (("字幕", "subtitle"), "制作字幕"),
        (("genvideo", "gen_video", "生成视频"), "生成视频"),
        (("genimage", "gen_image", "生成图片"), "生成图片"),
        (("分镜", "脚本", "storyboard"), "脚本分镜"),
        (("visualsearch", "generalsearch", "visual_search", "general_search", "检索", "查找参考"), "检索素材"),
    )
    for words, stage in rules:
        if any(word in lowered for word in words):
            return STAGES[stage]
    return ""


def stamp_ms(event: dict) -> int | None:
    try:
        value = event.get("processed_at")
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
    except (AttributeError, TypeError, ValueError):
        return None


def current_events(events: list[dict], started_at: int = 0, previous_user_id: str = "") -> list[dict]:
    # 必须存在本轮 user.message，防止发送前失败或旧终态冒充本轮完成。
    starts = [index for index, event in enumerate(events) if event.get("type") == "user.message"]
    if not starts:
        return []
    latest = events[starts[-1]]
    if (
        previous_user_id and latest.get("id") == previous_user_id
        or started_at and (stamp_ms(latest) or 0) < started_at
    ):
        return []
    return events[starts[-1]:]


def declared_from_events(events: list[dict], *, final: bool) -> set[str] | None:
    replies = [event_text(event) for event in events if event.get("type") == "agent.message"]
    return declared_from_replies(replies, final=final)


def declared_from_replies(replies: list[str], *, final: bool) -> set[str] | None:
    if final and replies:
        names = deliverables(replies[-1], final=True)
        if names is not None or "[deliverables]" in replies[-1]:
            return names
    for reply in reversed(replies):
        names = deliverables(reply)
        if names is not None or "[deliverables]" in reply:
            return names
    return None


def select_files(files: list[dict], names: set[str]) -> list[dict]:
    """真实文件名精确匹配；同名文件选择最新登记版本，绝不按扩展名猜交付。"""
    newest: dict[str, dict] = {}
    for item in files:
        name = item.get("filename") or item.get("name")
        if name not in names or not item.get("id") or item.get("status") not in (None, "active"):
            continue
        previous = newest.get(name)
        if previous is None or file_time(item) >= file_time(previous):
            newest[name] = item
    return list(newest.values())


def file_time(item: dict) -> float:
    value = item.get("created_at") or 0
    try:
        return float(value)
    except (ValueError, TypeError):
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0
