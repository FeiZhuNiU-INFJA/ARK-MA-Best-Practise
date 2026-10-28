"""DataHub 批量标注适配层 — 供 annotate handler 在 ≥ 阈值时走批量引擎。

设计（类比 ssjh.py / semantic.py）：
  - `api_available()`：有 DATAHUB_API_KEY 且仓内 blueai-datahub-skill 的 SDK 可导入。
  - `batch_annotate()`：把若干行写成 CSV（utf-8 无 BOM，规避 SKILL.md 记录的 BOM 陷阱）
    → 上传 → 建任务（prompt 用 `{{列名}}` 占位、require_json）→ 轮询到完成 → 解析每行
    `llm_result` JSON → 按行 id 回填，返回 {id: parsed_dict | {"_error": msg}}。
  - 任务级失败（上传/建任/超时/任务 FAILED）抛 DataHubError，由上层 annotate 决定降级；
    **行级失败不抛**，以 `{"_error": ...}` 标记，交由上层覆盖检查判定。

凭据只从环境变量读（生产由 MA Vault 注入）：DATAHUB_API_KEY —— 缺失即判定不可用。
平台无任务列表接口、成功不代表每行成功（看 failed/empty/unprocessed）等实测口径见 SKILL.md。
"""
from __future__ import annotations

import csv
import io
import json
import os
import sys
from typing import Any, Callable, Optional

# 仓内 datahub skill 根目录：ma-resources/skills/blueai-datahub-skill
_ORCH_DIR = os.path.dirname(os.path.abspath(__file__))
_SKILLS_DIR = os.path.dirname(_ORCH_DIR)
DATAHUB_DIR = os.path.join(_SKILLS_DIR, "blueai-datahub-skill")

# 轮次一/二默认批量标注模型（与客户 annotation_response 样例一致：gpt-4o-mini）。
_DEFAULT_MODEL = os.environ.get("DATAHUB_ANNOTATE_MODEL") or "gpt-4o-mini"


def api_available() -> bool:
    """是否具备 DataHub 真调用条件：有 token 且 SDK 入口可导入。"""
    if not os.environ.get("DATAHUB_API_KEY"):
        return False
    return os.path.isfile(os.path.join(DATAHUB_DIR, "sdk", "client.py"))


def _load_client():
    """惰性导入仓内 datahub skill 的 DataHubClient / DataHubError。"""
    sdk_dir = os.path.join(DATAHUB_DIR, "sdk")
    for p in (DATAHUB_DIR, sdk_dir):
        if p not in sys.path:
            sys.path.insert(0, p)
    from client import DataHubClient, DataHubError  # noqa: E402
    return DataHubClient, DataHubError


def _rows_to_csv(rows: list[dict], columns: list[str]) -> str:
    """把行写成临时 CSV（utf-8 无 BOM），返回文件路径。

    每行写入 columns 指定的列；统一补一列 `id`（标注回填键，取 row['_annot_id']）。
    用 utf-8（**非 utf-8-sig**）规避 DataHub 对 BOM 的解析陷阱（见 SKILL.md）。
    """
    import tempfile

    header = ["id"] + [c for c in columns if c != "id"]
    fd, path = tempfile.mkstemp(suffix=".csv", prefix="datahub_annot_")
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            out = {"id": str(r.get("_annot_id"))}
            for c in columns:
                if c == "id":
                    continue
                v = r.get(c)
                out[c] = "" if v is None else str(v)
            writer.writerow(out)
    return path


def _parse_llm_result(raw: Any) -> dict:
    """解析单行 llm_result（可能是 JSON 字符串/已是 dict/被围栏包裹）。失败标 _error。"""
    if isinstance(raw, dict):
        return raw
    text = str(raw or "").strip()
    if not text:
        return {"_error": "empty_llm_result"}
    # 剥 markdown 围栏
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else {"_value": obj}
    except (json.JSONDecodeError, ValueError):
        start, end = text.find("{"), text.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(text[start:end + 1])
            except (json.JSONDecodeError, ValueError):
                pass
        return {"_error": "unparseable_llm_result", "_raw": text[:200]}


def batch_annotate(
    rows: list[dict],
    prompt: str,
    *,
    content_columns: Optional[list[str]] = None,
    model: Optional[str] = None,
    timeout: int = 1800,
    interval: int = 10,
    page_size: int = 200,
) -> dict[str, dict]:
    """DataHub 批量标注（真路径）。返回 {annot_id: parsed_dict | {"_error": ...}}。

    Args:
      rows:  待标注行（每行需已写入 `_annot_id` 作回填键）。
      prompt: 标注 prompt，用 `{{列名}}` 占位（列名须与 content_columns 对应）。
      content_columns: 写入 CSV 的内容列（默认 ["content", "title"]）。
      model: DataHub 模型 id（默认 gpt-4o-mini）。

    任务级失败抛 DataHubError；行级失败以 {"_error": ...} 标记不抛。
    """
    DataHubClient, DataHubError = _load_client()
    cols = content_columns or ["content", "title"]
    csv_path = _rows_to_csv(rows, cols)
    try:
        client = DataHubClient()  # 凭据从环境变量 DATAHUB_API_KEY 读
        ds_id = client.upload_file(csv_path)
        task_id = client.create_task(
            ds_id, prompt, model=model or _DEFAULT_MODEL, require_json=True)
        client.wait_for_task(task_id, timeout=timeout, interval=interval)
        # 分页拉全部结果（平台无任务列表接口，按 page_num 翻页）。
        merged: dict[str, dict] = {}
        page = 1
        while True:
            data = client.get_task(task_id, page_num=page, page_size=page_size)
            result_list = data.get("result_list") or []
            if not result_list:
                break
            for item in result_list:
                rid = str(item.get("id"))
                if item.get("llm_error_message"):
                    merged[rid] = {"_error": str(item["llm_error_message"])[:200]}
                else:
                    merged[rid] = _parse_llm_result(item.get("llm_result"))
            if len(result_list) < page_size:
                break
            page += 1
        return merged
    finally:
        try:
            os.remove(csv_path)
        except OSError:
            pass
