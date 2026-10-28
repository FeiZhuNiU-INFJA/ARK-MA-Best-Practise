"""Topic5 G1 需求问卷的纯数据契约。

本模块只依赖标准库，负责：
  - 构造飞书 schema 2.0 表单元素；
  - 把 card.action.trigger 的 form_value 归一化为 pipeline 可消费的需求快照；
  - 校验必填字段并派生 SSJH / 活动明细清洗需要的字段。

文件上传不由卡片承担。用户需在同一话题发送 .xlsx，Gateway 会把它挂载到
``/mnt/session/uploads/topic5_activity_detail.xlsx``；G1 提交时另行校验已挂载状态。
"""
from __future__ import annotations

import io
import re
import zipfile
from typing import Any, Callable, Optional

PLATFORM_OPTIONS = (
    ("小红书", "xiaohongshu"),
    ("抖音", "douyin"),
    ("微博", "weibo"),
    ("哔哩哔哩/B站", "bilibili"),
    ("微信", "weixin"),
)
CONTENT_SCOPE_OPTIONS = (
    ("全部", "all"),
    ("仅 UGC", "ugc"),
    ("仅 PGC", "pgc"),
)
PLATFORM_LABELS = {value: label for label, value in PLATFORM_OPTIONS}
CONTENT_SCOPE_LABELS = {value: label for label, value in CONTENT_SCOPE_OPTIONS}

OPTIONAL_REQUIREMENT_DEFAULTS = {
    "business_questions": (
        "活动实际表现如何；哪些内容和参与方式有效；下次如何改进"
    ),
    "delivery_expectation": (
        "飞书营销活动复盘报告，包含可复核数据、图表和建议"
    ),
    "allowed_actions": (
        "读取本任务数据；调用已登记取数/标注/图表/飞书能力"
    ),
}

DEMO_REQUIREMENT_DEFAULTS = {
    "brand": "瑞幸",
    "activity_name": "瑞幸×暹罗厘普联名活动",
    "period_start": "2026-09-28",
    "period_end": "2026-10-07",
    "platforms": ["xiaohongshu"],
    "brand_query": "瑞幸",
    "brand_filter": "无",
    "activity_query": "瑞幸+(暹罗厘普|多栋|厘普|比格)",
    "activity_filter": "无",
    "product_query": "瑞幸+流心芝士拿铁",
    "content_scope": "all",
    **OPTIONAL_REQUIREMENT_DEFAULTS,
}

REQUIRED_FIELDS = (
    "brand",
    "activity_name",
    "period_start",
    "period_end",
    "platforms",
    "brand_query",
    "activity_query",
)

FIELD_LABELS = {
    "brand": "品牌名称",
    "activity_name": "活动名称",
    "period_start": "活动开始日期",
    "period_end": "活动结束日期",
    "platforms": "目标平台",
    "brand_query": "品牌取数关键词",
    "activity_query": "活动取数关键词",
    "product_query": "产品关键词",
    "content_scope": "内容范围",
    "business_questions": "希望回答的业务问题",
    "delivery_expectation": "交付期望",
    "allowed_actions": "允许执行的动作",
}


class IntakeValidationError(ValueError):
    """G1 表单字段不完整或不合法。"""


def validate_xlsx_container(
    data: bytes,
    *,
    max_entries: int = 2_000,
    max_uncompressed_bytes: int = 250 * 1024 * 1024,
) -> None:
    """校验 XLSX/ZIP 基本结构并拦截路径穿越、超大解压体积。"""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            names = {item.filename for item in infos}
            required = {"[Content_Types].xml", "xl/workbook.xml"}
            if not required.issubset(names):
                raise IntakeValidationError("文件不是有效的 .xlsx 工作簿")
            if len(infos) > max_entries:
                raise IntakeValidationError("Excel 内部文件数超过安全上限")
            total = 0
            for item in infos:
                path = item.filename.replace("\\", "/")
                if path.startswith("/") or ".." in path.split("/"):
                    raise IntakeValidationError("Excel 包含非法内部路径")
                total += int(item.file_size or 0)
                if total > max_uncompressed_bytes:
                    raise IntakeValidationError("Excel 解压后超过 250 MiB 安全上限")
    except zipfile.BadZipFile as error:
        raise IntakeValidationError("文件不是有效的 .xlsx 工作簿") from error


def _input(
    name: str,
    label: str,
    placeholder: str,
    *,
    required: bool = True,
    max_length: int = 300,
    default_value: str = "",
) -> dict:
    component = {
        "tag": "input",
        "name": name,
        "required": required,
        "input_type": "text",
        "max_length": max_length,
        "width": "fill",
        "label": {"tag": "plain_text", "content": label},
        "placeholder": {"tag": "plain_text", "content": placeholder},
    }
    if default_value:
        component["default_value"] = default_value
    return component


def _field_label(label: str) -> dict:
    return {"tag": "markdown", "content": f"**{label}**"}


def _date_picker(name: str, placeholder: str, *, initial_date: str = "") -> dict:
    component = {
        "tag": "date_picker",
        "name": name,
        "required": False,
        "width": "fill",
        "placeholder": {"tag": "plain_text", "content": placeholder},
    }
    if initial_date:
        component["initial_date"] = initial_date
    return component


def _select(
    name: str,
    label: str,
    options: tuple[tuple[str, str], ...],
    *,
    multiple: bool = False,
    required: bool = True,
    initial_option: str = "",
) -> dict:
    component = {
        "tag": "multi_select_static" if multiple else "select_static",
        "name": name,
        "required": required,
        "width": "fill",
        "placeholder": {"tag": "plain_text", "content": f"请选择{label}"},
        "options": [
            {"text": {"tag": "plain_text", "content": text}, "value": value}
            for text, value in options
        ],
    }
    if initial_option and not multiple:
        component["initial_option"] = initial_option
    return component


def build_requirement_form(
    event_id: int,
    action_value: Callable[[str], dict],
    defaults: Optional[dict[str, Any]] = None,
) -> dict:
    """构造 G1 需求问卷；提交按钮通过 action_value 注入幂等回调标识。"""
    values = dict(defaults or {})
    platform_defaults = values.get("platforms") or []
    if isinstance(platform_defaults, str):
        platform_defaults = [platform_defaults]
    return {
        "tag": "form",
        "name": f"requirement_form_{event_id}",
        "elements": [
            _input(
                "brand", "品牌名称*", "例如：瑞幸",
                required=False,
                default_value=str(values.get("brand") or ""),
            ),
            _input(
                "activity_name", "活动名称*", "例如：瑞幸×暹罗厘普联名活动",
                required=False,
                default_value=str(values.get("activity_name") or ""),
            ),
            _field_label("活动开始日期*"),
            _date_picker(
                "period_start", "请选择活动开始日期",
                initial_date=str(values.get("period_start") or ""),
            ),
            _field_label("活动结束日期*"),
            _date_picker(
                "period_end", "请选择活动结束日期",
                initial_date=str(values.get("period_end") or ""),
            ),
            _field_label("目标平台*"),
            _select(
                "platforms",
                "目标平台",
                PLATFORM_OPTIONS,
                multiple=not bool(platform_defaults),
                required=False,
                initial_option=str((platform_defaults or [""])[0]),
            ),
            _input(
                "brand_query", "品牌取数关键词*", "例如：瑞幸",
                required=False,
                default_value=str(values.get("brand_query") or ""),
            ),
            _input(
                "brand_filter",
                "品牌取数过滤词（可选）",
                "无过滤词可留空",
                required=False,
                default_value=str(values.get("brand_filter") or ""),
            ),
            _input(
                "activity_query",
                "活动取数关键词*",
                "例如：瑞幸+(暹罗厘普|多栋|厘普|比格)",
                max_length=500,
                required=False,
                default_value=str(values.get("activity_query") or ""),
            ),
            _input(
                "activity_filter",
                "活动取数过滤词（可选）",
                "无过滤词可留空",
                required=False,
                default_value=str(values.get("activity_filter") or ""),
            ),
            _input(
                "product_query", "产品关键词（可选）", "不做产品维度时可留空",
                required=False,
                default_value=str(values.get("product_query") or ""),
            ),
            _input(
                "business_questions",
                "希望回答的业务问题（可选）",
                "留空则默认分析活动表现、有效内容和后续改进",
                max_length=800,
                required=False,
                default_value=str(values.get("business_questions") or ""),
            ),
            _input(
                "delivery_expectation",
                "交付期望（可选）",
                "留空则默认交付含可复核数据、图表和建议的飞书复盘报告",
                max_length=500,
                required=False,
                default_value=str(values.get("delivery_expectation") or ""),
            ),
            _input(
                "allowed_actions",
                "允许执行的动作（可选）",
                "留空则按系统已登记的数据、标注、图表和飞书能力执行",
                max_length=500,
                required=False,
                default_value=str(values.get("allowed_actions") or ""),
            ),
            _field_label("内容范围（可选，默认全部）"),
            _select(
                "content_scope",
                "内容范围（可选，默认全部）",
                CONTENT_SCOPE_OPTIONS,
                required=False,
                initial_option=str(values.get("content_scope") or "all"),
            ),
            {
                "tag": "button",
                "name": "requirement_submit",
                "text": {"tag": "plain_text", "content": "确认需求并进入方案"},
                "type": "primary_filled",
                "width": "fill",
                "action_type": "form_submit",
                "value": action_value("pass"),
            },
        ],
    }


def _unwrap(value: Any) -> Any:
    """兼容飞书表单值的字符串、数组及 {value/key/id} 包装。"""
    if isinstance(value, dict):
        for key in ("value", "key", "id"):
            if key in value:
                return _unwrap(value[key])
    if isinstance(value, list):
        return [_unwrap(item) for item in value]
    return value


def _clean_text(value: Any) -> str:
    value = _unwrap(value)
    if value is None:
        return ""
    if isinstance(value, list):
        return ",".join(str(item).strip() for item in value if str(item).strip())
    text = str(value).strip()
    return "" if text.lower() in {"无", "none", "null", "n/a"} else text


def _clean_list(value: Any) -> list[str]:
    value = _unwrap(value)
    if isinstance(value, list):
        items = value
    else:
        items = re.split(r"[,，、\s]+", str(value or ""))
    return list(dict.fromkeys(str(item).strip() for item in items if str(item).strip()))


def query_terms(expression: str) -> list[str]:
    """把 ``瑞幸+(暹罗厘普|多栋)`` 拆成可用于提及判定的字面词。"""
    parts = re.split(r"[+|｜(),（）\s]+", expression or "")
    return list(dict.fromkeys(part.strip() for part in parts if part.strip()))


def normalize_requirement(form_value: dict[str, Any]) -> dict:
    """归一化 G1 表单，并派生后续节点直接消费的计划字段。"""
    raw = dict(form_value or {})
    normalized = {
        "brand": _clean_text(raw.get("brand")),
        "activity_name": _clean_text(raw.get("activity_name")),
        "period_start": _clean_text(raw.get("period_start"))[:10],
        "period_end": _clean_text(raw.get("period_end"))[:10],
        "platforms": _clean_list(raw.get("platforms")),
        "brand_query": _clean_text(raw.get("brand_query")),
        "brand_filter": _clean_text(raw.get("brand_filter")),
        "activity_query": _clean_text(raw.get("activity_query")),
        "activity_filter": _clean_text(raw.get("activity_filter")),
        "product_query": _clean_text(raw.get("product_query")),
        "content_scope": _clean_text(raw.get("content_scope")).lower() or "all",
        "business_questions": (
            _clean_text(raw.get("business_questions"))
            or OPTIONAL_REQUIREMENT_DEFAULTS["business_questions"]
        ),
        "delivery_expectation": (
            _clean_text(raw.get("delivery_expectation"))
            or OPTIONAL_REQUIREMENT_DEFAULTS["delivery_expectation"]
        ),
        "allowed_actions": (
            _clean_text(raw.get("allowed_actions"))
            or OPTIONAL_REQUIREMENT_DEFAULTS["allowed_actions"]
        ),
        "budget_constraint": _clean_text(raw.get("budget_constraint")),
    }
    missing = [FIELD_LABELS[key] for key in REQUIRED_FIELDS if not normalized.get(key)]
    if missing:
        raise IntakeValidationError("缺少必填字段：" + "、".join(missing))
    if normalized["period_start"] > normalized["period_end"]:
        raise IntakeValidationError("活动开始日期不能晚于结束日期")
    if normalized["content_scope"] not in {"all", "ugc", "pgc"}:
        raise IntakeValidationError("内容范围必须是全部、仅 UGC 或仅 PGC")

    brand_words = query_terms(normalized["brand_query"]) or [normalized["brand"]]
    activity_terms = [
        term for term in query_terms(normalized["activity_query"])
        if term not in set(brand_words)
    ]
    product_words = [
        term for term in query_terms(normalized["product_query"])
        if term not in set(brand_words)
    ]
    normalized.update(
        {
            "brand_words": brand_words,
            "product_words": product_words,
            "keywords": brand_words,
            "campaigns": [
                {
                    "id": normalized["activity_name"],
                    "name": normalized["activity_name"],
                    "filter_words": activity_terms,
                }
            ],
            "business_questions": [
                item.strip()
                for item in re.split(
                    r"[；;。\\n]+", normalized["business_questions"]
                )
                if item.strip()
            ],
        }
    )
    return normalized


def summarize_requirement(requirement: dict) -> str:
    """卡片终态/日志使用的紧凑摘要，避免直接展示整段 JSON。"""
    scope = {"all": "全部", "ugc": "仅 UGC", "pgc": "仅 PGC"}.get(
        str(requirement.get("content_scope") or ""), "未指定"
    )
    platforms = "、".join(requirement.get("platforms") or [])
    return (
        f"{requirement.get('brand', '')} · {requirement.get('activity_name', '')} · "
        f"{requirement.get('period_start', '')}～{requirement.get('period_end', '')} · "
        f"{platforms} · {scope}"
    )


def _escape_markdown(value: Any) -> str:
    text = str(value or "").strip()
    for char in ("\\", "`", "*", "_", "[", "]", "<", ">"):
        text = text.replace(char, f"\\{char}")
    return text


def requirement_snapshot_rows(requirement: dict) -> list[tuple[str, str]]:
    """把冻结的 G1 requirement_confirmed 转成用户可读字段，不暴露内部 ID/路径。"""
    source_file = requirement.get("source_file") or {}
    platforms = "、".join(
        PLATFORM_LABELS.get(str(value), str(value))
        for value in requirement.get("platforms") or []
    )
    scope = CONTENT_SCOPE_LABELS.get(
        str(requirement.get("content_scope") or "all"),
        str(requirement.get("content_scope") or "all"),
    )
    questions = requirement.get("business_questions") or []
    if isinstance(questions, str):
        questions = [questions]
    return [
        ("活动明细文件", f"{source_file.get('name') or '未记录'}（已挂载）"),
        ("品牌", requirement.get("brand") or "未填写"),
        ("活动", requirement.get("activity_name") or "未填写"),
        (
            "活动周期",
            f"{requirement.get('period_start') or '未填写'} ～ "
            f"{requirement.get('period_end') or '未填写'}",
        ),
        ("目标平台", platforms or "未填写"),
        ("品牌取数关键词", requirement.get("brand_query") or "未填写"),
        ("品牌过滤词", requirement.get("brand_filter") or "无"),
        ("活动取数关键词", requirement.get("activity_query") or "未填写"),
        ("活动过滤词", requirement.get("activity_filter") or "无"),
        ("产品关键词", requirement.get("product_query") or "未填写"),
        ("内容范围", scope),
        ("业务问题", "；".join(str(item) for item in questions) or "未填写"),
        ("交付期望", requirement.get("delivery_expectation") or "未填写"),
        ("允许动作", requirement.get("allowed_actions") or "未填写"),
    ]


def format_requirement_snapshot(requirement: dict, *, heading: bool = True) -> str:
    """渲染飞书 Markdown；终态卡与运行进度卡复用同一份展示口径。"""
    lines = [
        f"- **{label}**：{_escape_markdown(value)}"
        for label, value in requirement_snapshot_rows(requirement)
    ]
    if heading:
        return "**G1 需求快照**\n" + "\n".join(lines)
    return "\n".join(lines)
