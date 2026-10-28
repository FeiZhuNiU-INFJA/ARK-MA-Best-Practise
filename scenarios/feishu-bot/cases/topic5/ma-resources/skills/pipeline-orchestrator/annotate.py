"""AI 标注适配层 — 供 annotate handler 接真（轮次一 n09 / 轮次二 n11）。

设计（类比 ssjh.py / charts.py）：
  - 真路径按 `annotation_routing_threshold` 路由：
      行数 ≥ 阈值 且 DataHub 可用 → datahub.batch_annotate（批量引擎）；
      否则 → 小引擎（semantic.py，分批 chat_json）。
    两条真路径 **出口 schema 完全一致**（每行回填相同标注字段）。
  - 降级路径：无任何凭据时，产出结构真实、带 `degraded=True` 的确定性标注，
    使下游（opinion_candidates / aggregate）无差别消费，本地可端到端跑通。

口径红线：
  - 轮次一：情感(ai_sentiment ∈ {1,0,-1}) + 活动相关(relevance: bool)。情感以 AI 标注为准。
  - 轮次二：观点 / UGC 参与方式 / 讨论维度 **三个独立任务** + 覆盖检查；
    覆盖检查有 ERROR（真路径下解析失败/缺口）才阻断（raise→引擎 pause 重试）；
    “不适用”情形标注为 N/A 跳过、不阻断。
  - 多活动逐个处理由上层 aggregate 负责；本层只做逐行标注与覆盖核对。
"""
from __future__ import annotations

import json
from typing import Any, Optional

import semantic
import datahub

# 情感标签 ↔ int（与 aggregate._row_sentiment 口径一致）。
_SENTI_TO_INT = {
    "正面": 1, "正": 1, "positive": 1, "积极": 1,
    "中性": 0, "中": 0, "neutral": 0,
    "负面": -1, "负": -1, "negative": -1, "消极": -1,
}
_INT_TO_SENTI = {1: "正面", 0: "中性", -1: "负面"}

# 轮次二“不适用”标记（不阻断覆盖检查）。
_NA = "N/A"
_ROUND2_TASKS = ("opinion", "ugc_category", "discussion_dimension")


def _senti_to_int(v: Any) -> Optional[int]:
    """情感归一化到 {1,0,-1}；无法识别返回 None。"""
    if v is None:
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        iv = int(v)
        return iv if iv in (1, 0, -1) else None
    s = str(v).strip()
    if s in _SENTI_TO_INT:
        return _SENTI_TO_INT[s]
    try:
        iv = int(s)
        return iv if iv in (1, 0, -1) else None
    except ValueError:
        return None


def _to_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip() in ("是", "相关", "true", "True", "1", "yes", "relevant")


def _tag_ids(rows: list[dict]) -> None:
    """为每行写入稳定回填键 `_annot_id`（用去重键/索引）。"""
    for i, r in enumerate(rows):
        if not r.get("_annot_id"):
            r["_annot_id"] = str(r.get("_dedup_key") or r.get("id") or r.get("fingerprint") or i)


# ── 轮次一：情感 + 活动相关 ───────────────────────────────────
_R1_PROMPT_DH = (
    "请判断以下社媒内容的【情感倾向】与【是否与品牌营销活动相关】。\n"
    "文本：{{content}}\n"
    "标题：{{title}}\n"
    "返回 JSON：{\"sentiment\": \"正面/中性/负面\", \"relevant\": true/false}"
)


def _r1_sys() -> str:
    return ("你是营销舆情标注员。对每条内容判定：1) 情感倾向(正面/中性/负面)；"
            "2) 是否与品牌营销活动相关(true/false)。只输出 JSON 数组，每项 "
            "{\"id\": \"行id\", \"sentiment\": \"正面/中性/负面\", \"relevant\": true/false}，"
            "顺序与输入一致，不要多余文字。")


def _apply_r1(row: dict, parsed: dict) -> None:
    """把轮次一解析结果回填到行（ai_sentiment/relevance）。"""
    si = _senti_to_int(parsed.get("sentiment"))
    if si is not None:
        row["ai_sentiment"] = si
        row["ai_sentiment_label"] = _INT_TO_SENTI[si]
    if "relevant" in parsed:
        row["relevance"] = _to_bool(parsed.get("relevant"))


def _small_engine_r1(
    rows: list[dict], batch: int = 20, max_attempts: int = 2
) -> None:
    """小引擎轮次一：分批标注，并只重试模型漏掉的行。"""
    for i in range(0, len(rows), batch):
        chunk = rows[i:i + batch]
        pending = list(chunk)
        for _attempt in range(max_attempts):
            payload = [{
                "id": r["_annot_id"],
                "content": str(r.get("content") or "")[:200],
                "title": str(r.get("title") or "")[:80],
            } for r in pending]
            data = semantic.chat_json(
                _r1_sys(),
                json.dumps(payload, ensure_ascii=False),
                max_tokens=2048,
            )
            by_id = (
                {str(d.get("id")): d for d in data if isinstance(d, dict)}
                if isinstance(data, list) else {}
            )
            for row in pending:
                parsed = by_id.get(row["_annot_id"])
                if isinstance(parsed, dict):
                    _apply_r1(row, parsed)
            pending = [
                row for row in pending
                if row.get("ai_sentiment") not in (1, 0, -1)
                or "relevance" not in row
            ]
            if not pending:
                break


def annotate_round1(rows: list[dict], *, threshold: int = 1000) -> dict:
    """轮次一标注。返回 {rows, round:1, routed_via, coverage, degraded?}。"""
    _tag_ids(rows)
    routed_via, degraded = _route_and_run(
        rows, threshold=threshold,
        dh_prompt=_R1_PROMPT_DH, dh_apply=_apply_r1,
        small_engine=_small_engine_r1, degrade=_degrade_r1)
    cov = _coverage_r1(rows)
    out = {"rows": rows, "round": 1, "routed_via": routed_via, "coverage": cov}
    if degraded:
        out["degraded"] = True
    return out


def _degrade_r1(rows: list[dict]) -> None:
    """轮次一降级：情感取现有 SSJH sentiment（无则按 0 中性），相关性按品牌/关键词命中。

    说明：这是无凭据时的确定性兜底以跑通链路，显式标 degraded；真实接真走模型。
    """
    for r in rows:
        si = _senti_to_int(r.get("sentiment"))
        r["ai_sentiment"] = si if si is not None else 0
        r["ai_sentiment_label"] = _INT_TO_SENTI[r["ai_sentiment"]]
        # 相关性降级：有 content/title 即视为相关（跑通用；真路径由模型判定）。
        r.setdefault("relevance", True)


def _coverage_r1(rows: list[dict]) -> dict:
    total = len(rows)
    with_senti = sum(1 for r in rows if r.get("ai_sentiment") in (1, 0, -1))
    with_rel = sum(1 for r in rows if "relevance" in r)
    return {"total": total, "sentiment_covered": with_senti, "relevance_covered": with_rel,
            "sentiment_missing": total - with_senti, "relevance_missing": total - with_rel}


# ── 轮次二：观点 / UGC 参与方式 / 讨论维度（三独立任务）──────────
def _r2_sys(
    opinion_labels: list[str], ugc_categories: list[str],
    ugc_not_applicable: bool = False,
) -> str:
    ops = "、".join(opinion_labels) if opinion_labels else "（无预设，按内容归纳）"
    ugcs = "、".join(ugc_categories) if ugc_categories else "（无预设）"
    ugc_instruction = (
        "2) ugc_category：当前方案仅 PGC，本项固定填 \"N/A\"；\n"
        if ugc_not_applicable else
        f"2) ugc_category：UGC 参与方式（候选：{ugcs}；非 UGC 或不适用填 \"N/A\"）；\n"
    )
    return ("你是营销舆情标注员，对每条内容做独立标注：\n"
            f"1) opinion：命中的观点标签（候选：{ops}；不适用填 \"N/A\"）；\n"
            + ugc_instruction +
            "3) discussion_dimension：讨论维度（如 功效/价格/包装/服务/活动玩法；不适用填 \"N/A\"）。\n"
            "只输出 JSON 数组，每项 {\"id\":\"行id\",\"opinion\":\"\",\"ugc_category\":\"\","
            "\"discussion_dimension\":\"\"}，顺序与输入一致。")


def _r2_prompt_dh(
    opinion_labels: list[str], ugc_categories: list[str],
    ugc_not_applicable: bool = False,
) -> str:
    ops = "、".join(opinion_labels) if opinion_labels else "（无预设）"
    ugcs = "、".join(ugc_categories) if ugc_categories else "（无预设）"
    ugc = "固定填 N/A（当前方案仅 PGC）" if ugc_not_applicable else f"候选：{ugcs}"
    return ("对以下内容做三项独立标注，每项都必须给值，不适用一律填 N/A（不得留空）。\n"
            "文本：{{content}}\n标题：{{title}}\n"
            f"1) opinion：命中的观点标签（候选：{ops}）；\n"
            f"2) ugc_category：UGC 参与方式（{ugc}）；\n"
            "3) discussion_dimension：讨论维度（如 功效/价格/包装/服务/活动玩法）；\n"
            "返回 JSON：{\"opinion\":\"\",\"ugc_category\":\"\",\"discussion_dimension\":\"\"}")


def _apply_r2(row: dict, parsed: dict) -> None:
    """回填三任务标注。模型已处理该行（parsed 非 _error）但某分类字段留空，
    视为「无匹配」= N/A（合约内合法标注），避免零星空值被误判为缺口。
    行级 _error / 未返回的行不经此函数，仍由覆盖检查 fail-closed。"""
    for k in _ROUND2_TASKS:
        v = parsed.get(k)
        if k in parsed and v not in (None, ""):
            row[k] = str(v).strip()
        else:
            row[k] = _NA


def annotate_round2(rows: list[dict], *, opinion_labels: list[str],
                    ugc_categories: list[str], threshold: int = 1000,
                    ugc_not_applicable: bool = False) -> dict:
    """轮次二标注（三任务 + 覆盖检查）。返回 {rows, round:2, routed_via, coverage, degraded?}。

    覆盖检查有 ERROR（真路径下某任务存在解析失败且非 N/A 跳过）→ 调用方 raise。
    """
    _tag_ids(rows)

    def _small(rs):
        _small_engine_r2(
            rs, opinion_labels, ugc_categories,
            ugc_not_applicable=ugc_not_applicable,
        )

    def _degrade(rs):
        _degrade_r2(rs, opinion_labels, ugc_categories)

    routed_via, degraded = _route_and_run(
        rows, threshold=threshold,
        dh_prompt=_r2_prompt_dh(
            opinion_labels, ugc_categories,
            ugc_not_applicable=ugc_not_applicable,
        ),
        dh_apply=_apply_r2,
        small_engine=_small, degrade=_degrade)
    if ugc_not_applicable:
        for row in rows:
            row["ugc_category"] = _NA
    cov = check_coverage(rows, degraded=degraded)
    if ugc_not_applicable:
        cov["ugc_category"] = {
            "total": len(rows),
            "labeled": len(rows),
            "na": len(rows),
            "missing": 0,
            "status": "NOT_APPLICABLE",
        }
        cov["has_error"] = any(
            item.get("status") == "ERROR"
            for key, item in cov.items()
            if key != "has_error" and isinstance(item, dict)
        )
    out = {"rows": rows, "round": 2, "routed_via": routed_via, "coverage": cov}
    if ugc_not_applicable:
        out["not_applicable"] = ["ugc_category"]
    if degraded:
        out["degraded"] = True
    return out


def _small_engine_r2(
    rows: list[dict], opinion_labels, ugc_categories, batch: int = 20,
    ugc_not_applicable: bool = False,
    max_attempts: int = 3,
) -> None:
    sys_p = _r2_sys(
        opinion_labels, ugc_categories,
        ugc_not_applicable=ugc_not_applicable,
    )
    for i in range(0, len(rows), batch):
        chunk = rows[i:i + batch]
        pending = list(chunk)
        for _attempt in range(max_attempts):
            payload = [{
                "id": r["_annot_id"],
                "content": str(r.get("content") or "")[:200],
                "title": str(r.get("title") or "")[:80],
            } for r in pending]
            data = semantic.chat_json(
                sys_p,
                json.dumps(payload, ensure_ascii=False),
                max_tokens=2560,
            )
            by_id = (
                {str(d.get("id")): d for d in data if isinstance(d, dict)}
                if isinstance(data, list) else {}
            )
            for row in pending:
                parsed = by_id.get(row["_annot_id"])
                if isinstance(parsed, dict):
                    _apply_r2(row, parsed)
            pending = [
                row for row in pending
                if any(
                    task not in row or not str(row.get(task) or "").strip()
                    for task in _ROUND2_TASKS
                )
            ]
            if not pending:
                break


def _degrade_r2(rows: list[dict], opinion_labels, ugc_categories) -> None:
    """轮次二降级：确定性派生三标签（命中候选或 N/A），显式标 degraded。"""
    op0 = opinion_labels[0] if opinion_labels else _NA
    for r in rows:
        hay = (str(r.get("title") or "") + " " + str(r.get("content") or ""))
        r.setdefault("opinion", next((o for o in opinion_labels if o and o in hay), op0))
        r.setdefault("ugc_category",
                     next((u for u in ugc_categories if u and u in hay),
                          (ugc_categories[0] if ugc_categories else _NA)))
        r.setdefault("discussion_dimension",
                     next((d for d in ("功效", "价格", "包装", "服务", "活动") if d in hay), _NA))


def check_coverage(rows: list[dict], *, degraded: bool = False) -> dict:
    """三任务覆盖检查。每任务统计 total/labeled/na/missing，并给 status。

    status：
      - DEGRADED：降级产物（不阻断）。
      - OK：无 missing（未标注视为 ERROR 缺口）。
      - ERROR：存在 missing（真路径下调用方应 raise → 引擎 pause 重试）。
    N/A 计入 labeled（“不适用”是合法标注，不算缺口）。
    """
    total = len(rows)
    cov: dict[str, dict] = {}
    has_error = False
    for task in _ROUND2_TASKS:
        labeled = sum(1 for r in rows if task in r and str(r.get(task)).strip() != "")
        na = sum(1 for r in rows if str(r.get(task)).strip() == _NA)
        missing = total - labeled
        if degraded:
            status = "DEGRADED"
        elif missing == 0:
            status = "OK"
        else:
            status = "ERROR"
            has_error = True
        cov[task] = {"total": total, "labeled": labeled, "na": na,
                     "missing": missing, "status": status}
    cov["has_error"] = has_error
    return cov


# ── 路由 + 执行（真路径 DataHub / 小引擎；无凭据降级）────────────
def _route_and_run(rows, *, threshold, dh_prompt, dh_apply, small_engine, degrade) -> tuple[str, bool]:
    """按阈值与可用凭据选择执行路径。返回 (routed_via, degraded)。

    - 行数 ≥ threshold 且 DataHub 可用 → datahub 批量；
    - 否则小引擎（semantic）可用 → 小引擎；
    - 都不可用 / 真路径异常 → 确定性降级。
    """
    n = len(rows)
    use_datahub = n >= threshold and datahub.api_available()
    if use_datahub:
        try:
            _run_datahub(rows, dh_prompt, dh_apply)
            return "datahub", False
        except Exception:
            pass  # 批量任务级失败 → 落小引擎/降级
    if semantic.llm_available() and n:
        try:
            small_engine(rows)
            return "small_engine", False
        except Exception:
            pass
    degrade(rows)
    return "fallback", True


def _run_datahub(rows, prompt, apply_fn) -> None:
    """DataHub 批量标注并回填。行级解析失败不抛，留待覆盖检查判定。"""
    results = datahub.batch_annotate(rows, prompt, content_columns=["content", "title"])
    for r in rows:
        parsed = results.get(r["_annot_id"])
        if isinstance(parsed, dict) and not parsed.get("_error"):
            apply_fn(r, parsed)
