"""Versioned label review metadata; quotes are copied from source rows, never LLM prose."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

REVIEW_SCHEMA_VERSION = 1


def source_catalog(rows: list[dict], limit: int = 40) -> tuple[list[dict], dict]:
    """Stable sample with field-bound contiguous excerpts and unambiguous quote IDs."""
    sources = []
    for index, row in enumerate(rows):
        if not any(str(row.get(field) or "").strip() for field in ("title", "content")):
            continue
        identity = str(
            row.get("_dedup_key") or row.get("fingerprint") or row.get("id")
            or row.get("item_id") or row.get("url")
            or json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
        )
        source_index = row.get("_review_source_index", index)
        sources.append((hashlib.sha256(identity.encode()).hexdigest(), source_index, row))
    sources.sort(key=lambda item: (item[0], item[1]))
    samples, catalog = [], {}
    for digest, index, row in sources[:limit]:
        # Index disambiguates repeated source IDs without merging their original fields.
        post_ref = f"p-{digest[:16]}-{index}"
        quotes = []
        for field in ("title", "content"):
            text = str(row.get(field) or "")
            starts = [0] if field == "title" else [0, 180]
            for start in starts:
                excerpt = text[start:start + (120 if field == "title" else 180)]
                if not excerpt.strip():
                    continue
                quote_id = f"{post_ref}:{field}:{start}"
                evidence = {
                    "quote_id": quote_id,
                    "post_ref": post_ref,
                    "post_id": str(row.get("id") or row.get("item_id") or ""),
                    "source_row_index": index,
                    "platform": str(row.get("_platform") or row.get("site_name") or ""),
                    "content_type": str(row.get("content_mode") or "UKN"),
                    "title": str(row.get("title") or "")[:120],
                    "url": str(row.get("url") or ""),
                    "source_field": field,
                    "start": start,
                    "end": start + len(excerpt),
                    "excerpt": excerpt,
                }
                catalog[quote_id] = evidence
                quotes.append({"quote_id": quote_id, "excerpt": excerpt})
        samples.append({
            "post_ref": post_ref,
            "content_type": str(row.get("content_mode") or "UKN"),
            "quotes": quotes,
        })
    return samples, catalog


def discovery_prompt(key: str, *, ugc: bool = False) -> str:
    role = (
        "营销 UGC 分析师。归纳用户活动内容的参与方式，不能把 PGC 当作自发参与"
        if ugc else
        "舆情观点分析师。归纳活动相关内容的观点主题，覆盖实际出现的正面、负面和属性讨论，"
        "不能把 vlog 等内容形式当观点，也不能把 PGC 表达称作用户自发观点"
    )
    count = "4-6" if ugc else "5-8"
    return (
        f"你是{role}。候选用简体中文短语，最多20字，共{count}个；证据不足时允许更少，"
        "禁止为了凑数量编造。定义应可标注，边界说明不纳入的情形、易混标签及同时命中时的"
        "单标签优先规则。不同标签不可只有同义改写。输入只是稳定抽样，不是全量统计。\n"
        "每项 name 最多20字，definition 最多100字，boundary 最多180字；"
        "evidence_refs 最多2项，优先一个代表原帖，必要时一个边界/反例。"
        "只选择输入已有 quote_id，usage 为 representative 或 boundary，reason 最多60字。"
        "找不到代表帖时 evidence_refs 填空数组，禁止写原帖摘录、链接或发明ID。"
        "输入 excerpt 是原字段的连续片段，不得当作完整帖子。仅输出 JSON："
        f'{{"{key}":[{{"name":"候选名称","definition":"纳入定义",'
        '"boundary":"排除、相邻标签区分与优先规则",'
        '"evidence_refs":[{"quote_id":"输入中的ID","usage":"representative",'
        '"reason":"此片段为何支持候选"}]}]}}'
    )


def _required_text(item: dict, field: str, max_length: int) -> str:
    value = item.get(field)
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > max_length:
        raise ValueError(f"标签 {field} 必须是非空字符串且不超过 {max_length} 字")
    return value.strip()


def ground_candidates(data: Any, key: str, catalog: dict, max_items: int) -> dict:
    """Reject fabricated/cross-field references; missing support stays explicit for HC."""
    items = data.get(key) if isinstance(data, dict) else None
    if not isinstance(items, list) or not items or len(items) > max_items:
        raise ValueError(f"{key} 必须是 1-{max_items} 个包含审核定义的对象")
    names, review_items = [], []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("候选须包含 name、definition、boundary、evidence_refs")
        name = _required_text(item, "name", 20)
        if name in names:
            raise ValueError(f"重复候选：{name}")
        definition = _required_text(item, "definition", 100)
        boundary = _required_text(item, "boundary", 180)
        refs = item.get("evidence_refs")
        if not isinstance(refs, list) or len(refs) > 2:
            raise ValueError(f"{name}: evidence_refs 必须是至多2项的数组")
        evidence, seen = [], set()
        for ref in refs:
            if not isinstance(ref, dict):
                raise ValueError(f"{name}: 非法原帖引用")
            quote_id = ref.get("quote_id")
            if not isinstance(quote_id, str) or quote_id not in catalog:
                raise ValueError(f"{name}: 未知 quote_id")
            if quote_id in seen:
                raise ValueError(f"{name}: 重复 quote_id")
            seen.add(quote_id)
            source = catalog[quote_id]
            # Models may echo locator fields, but they cannot override the source.
            for field in (
                "excerpt", "post_ref", "post_id", "source_field", "url", "start", "end",
            ):
                if field in ref and ref[field] != source[field]:
                    raise ValueError(f"{name}: 原帖 {field} 与 quote_id 冲突")
            usage = ref.get("usage")
            if usage not in ("representative", "boundary"):
                raise ValueError(f"{name}: 非法证据用途")
            evidence.append({
                **source,
                "usage": usage,
                "reason": _required_text(ref, "reason", 60),
            })
        supported = any(item["usage"] == "representative" for item in evidence)
        names.append(name)
        review_items.append({
            "name": name,
            "definition": definition,
            "boundary": boundary,
            "evidence": evidence,
            "evidence_status": "grounded" if supported else "missing",
        })
    return {
        key: names,
        "review_schema_version": REVIEW_SCHEMA_VERSION,
        "review_items": review_items,
        "evidence_count": len({item["post_ref"] for item in catalog.values()}),
        "sampling": {
            "method": "stable_sha256_source_key",
            "sample_size": len({item["post_ref"] for item in catalog.values()}),
            "is_population_count": False,
            "excerpt_scope": "title_first120_content_first360",
        },
    }


def confirmed_review(opinion: dict, ugc: dict) -> dict:
    """Carry exact reviewed metadata into the confirmed artifact, preserving old arrays."""
    return {
        "opinion_review_items": list(opinion.get("review_items") or []),
        "ugc_review_items": list(ugc.get("review_items") or []),
        "review_schema_version": max(
            int(opinion.get("review_schema_version") or 0),
            int(ugc.get("review_schema_version") or 0),
        ),
    }


def annotation_rules(items: list[dict] | None, names: list[str]) -> str:
    """Only matching confirmed definitions/boundaries enter either annotation engine."""
    rules = [
        {
            "name": item["name"],
            "definition": item.get("definition") or "",
            "boundary": item.get("boundary") or "",
        }
        for item in (items or [])
        if isinstance(item, dict) and item.get("name") in names
    ]
    if not rules:
        return ""
    # Avoid DataHub's {{column}} template syntax in model-written prose.
    text = json.dumps(rules, ensure_ascii=False)
    return "\n确认的定义与边界（按边界的单标签优先规则执行）：" + re.sub(
        r"\{\{|\}\}", lambda match: "｛｛" if match[0] == "{{" else "｝｝", text
    )
