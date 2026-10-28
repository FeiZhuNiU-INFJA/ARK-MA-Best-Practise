"""topic5-validator 适配层 — 供 Gateway / 薄 Agent 在 n17_validate 关卡预填 qc_report。

n17 按无-schema-change 红线保持 mode:human（Validator 独立 Session 身份），qc_report 由
Validator 产出后由 Coordinator 自动续跑注入。本适配层把 topic5-validator skill 的
「全量扫描」逻辑（scripts/scan_report.py）惰性加载进来，使编排侧能确定性地产出
qc_report 基线，再由独立 Agent 按完整适配表执行全文语义检查（schema 一致）。

设计（类比 ssjh.py / datahub.py，零三方依赖，单一口径事实源在 skill 脚本）：
  - available()：能定位到 skill 脚本即可用（无外部凭据）。
  - validate_report(report_md)：产出 qc_report（has_p0/p0_count/issues/...）。
  - 定位失败/异常时退回极简内置红线扫描（degraded 标注），保证关卡始终拿得到 qc_report。
"""
from __future__ import annotations

import importlib.util
import os
import re
from pathlib import Path
from typing import Any, Optional

# 兜底红线（与 skill scan_report.py 对齐；仅在脚本不可定位时使用）。
_FORBIDDEN = ("人均", "转化率", "转化", "性价比")
_AGG_SUM = ("合计声量", "总声量", "声量总和", "累计声量", "声量合计")
_AGG_DISCLAIMER = ("不相加", "不合计", "逐个", "分别统计", "不得相加", "勿")
_INTERNAL_FIELDS = (
    "_annot_id", "_dedup_key", "_source_analysis_object",
    "ai_sentiment", "interaction_cnt", "discussion_dimension",
)
_RAW_JSON = re.compile(r"[\{\[]\s*\"[^\"\n]+\"\s*:")
_CORE_CHAPTER_PATTERNS = {
    "品牌传播": r"品牌传播", "活动传播": r"活动传播",
    "综合定位": r"综合.{0,4}定位", "经验总结": r"经验总结",
}

_SCAN = None  # 惰性缓存已加载的 scan_report 模块


def _skill_scan_path() -> Optional[Path]:
    """定位 topic5-validator/scripts/scan_report.py（允许环境变量覆盖）。"""
    env = os.environ.get("TOPIC5_VALIDATOR_SCAN")
    if env and Path(env).exists():
        return Path(env)
    # pipeline-orchestrator/ 与 topic5-validator/ 同为 skills/ 下兄弟目录。
    here = Path(__file__).resolve()
    cand = here.parent.parent / "topic5-validator" / "scripts" / "scan_report.py"
    return cand if cand.exists() else None


def _load_scan():
    global _SCAN
    if _SCAN is not None:
        return _SCAN
    path = _skill_scan_path()
    if not path:
        _SCAN = False
        return False
    spec = importlib.util.spec_from_file_location("topic5_validator_scan", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    _SCAN = mod
    return mod


def available() -> bool:
    return bool(_skill_scan_path())


def _markdown_and_degraded(report_md: Any) -> tuple[str, bool]:
    if isinstance(report_md, dict):
        return str(report_md.get("markdown") or ""), bool(report_md.get("degraded"))
    return str(report_md or ""), False


def validate_report(
    report_md: Any, *, requirement: Optional[dict] = None,
    stats: Optional[dict] = None, charts: Optional[dict] = None,
) -> dict:
    """对 report_md（dict 或 markdown 字符串）产出 qc_report（全量扫描基线）。"""
    md, degraded = _markdown_and_degraded(report_md)
    scan = _load_scan()
    if scan:
        expected_campaigns = list(((stats or {}).get("campaigns") or {}).keys())
        return scan.scan_markdown(
            md,
            degraded=degraded,
            expected_campaigns=expected_campaigns or None,
            requirement=requirement,
            stats=stats,
            charts=charts,
        )
    return _builtin_scan(md, degraded=degraded)


def _builtin_scan(md: str, *, degraded: bool) -> dict:
    """脚本不可定位时的兜底扫描（口径子集，标 degraded 以示降级）。"""
    issues: list[dict] = []
    for ln_no, line in enumerate((md or "").splitlines(), 1):
        for term in _FORBIDDEN:
            if term in line:
                issues.append({"id": f"P0-forbidden-{term}", "severity": "P0",
                               "title": f"出现禁用措辞「{term}」",
                               "evidence": f"L{ln_no}: {line.strip()}"[:300],
                               "locus": f"L{ln_no}"})
        if any(mk in line for mk in _AGG_DISCLAIMER):
            continue
        for pat in _AGG_SUM:
            if pat in line:
                issues.append({"id": f"P0-aggregate-{pat}", "severity": "P0",
                               "title": f"疑似多活动相加口径「{pat}」",
                               "evidence": f"L{ln_no}: {line.strip()}"[:300],
                               "locus": f"L{ln_no}"})
        leaked = [name for name in _INTERNAL_FIELDS if name in line]
        if _RAW_JSON.search(line) or leaked:
            issues.append({
                "id": f"P0-raw-json-{ln_no}",
                "severity": "P0",
                "title": "报告正文泄漏内部 JSON 或标注字段",
                "evidence": f"L{ln_no}: {line.strip()}"[:300],
                "locus": f"L{ln_no}",
            })
    body = (md or "").strip()
    if not body:
        issues.append({"id": "P0-empty", "severity": "P0", "title": "报告内容为空",
                       "evidence": "markdown 为空字符串", "locus": "structure"})
    else:
        for name, pat in _CORE_CHAPTER_PATTERNS.items():
            if not re.search(pat, md):
                issues.append({"id": f"P0-missing-chapter-{name}", "severity": "P0",
                               "title": f"缺核心章节「{name}」",
                               "evidence": f"未检出「{name}」", "locus": "structure"})
    p0 = [i for i in issues if i["severity"] == "P0"]
    return {"has_p0": bool(p0), "p0_count": len(p0),
            "p1_count": 0, "p2_count": 0, "issues": issues,
            "scan_mode": "builtin_fallback", "degraded": True}
