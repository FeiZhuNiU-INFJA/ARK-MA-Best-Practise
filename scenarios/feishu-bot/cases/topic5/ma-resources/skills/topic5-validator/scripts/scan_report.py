#!/usr/bin/env python3
"""topic5-validator 第一段：报告内容层全量确定性扫描器。

把一份报告内容层 MD（n16 产出的 report_md.markdown）按活动复盘口径红线做全量检查，
产出结构化 qc_report（含分级 issues、p0_count、has_p0），供 n17_validate 关卡注入、
由 n18_qc_gate 按 pass_policy=p0_count==0 路由。

两段式审计中本脚本负责「全量扫描」（机器可查的硬口径）；语义层「全文检查」
由 topic5-validator Agent 依规则适配表补充（命中的问题以同 schema 合并）。

用法：
  python3 scan_report.py --report-md /path/report_md.json --out /path/qc_report.json
  python3 scan_report.py --markdown-file /path/report.md --out /path/qc_report.json

分级（severity）：
  P0 禁发（has_p0=True → 必须返修）：出现禁用措辞（人均/转化率/转化/性价比）、
      出现非白名单“合计声量/总声量”相加口径、内部 JSON/标注字段泄漏、
      报告为空/缺核心章节。
  P1 可披露放行：图表引用缺失、降级产物标注、字数偏短等（记录但不拦截交付）。
  P2 提示：风格/措辞建议。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Optional


# ── 口径红线常量（与 handlers.py 对齐，单一事实源在 DAG params + knowledge/）──
FORBIDDEN_TERMS = ("人均", "转化率", "转化", "性价比")
METRIC_WHITELIST = ("条均互动量", "品牌提及率", "产品提及占比", "NSR")
# 相加合计声量：多活动“不相加”，出现这些措辞视为口径违规（P0）。
AGGREGATE_SUM_PATTERNS = ("合计声量", "总声量", "声量总和", "累计声量", "声量合计")
# 否定/免责标记：同一行出现时说明是“不相加”口径声明而非违规断言，跳过。
AGGREGATE_DISCLAIMER_MARKERS = ("不相加", "不合计", "逐个", "分别统计", "不得相加", "勿")
# 五章结构核心章节：用正则容忍“综合表现定位”等变体（report_chapters=five）；缺任一核心章记 P0。
CORE_CHAPTER_PATTERNS = {
    "品牌传播": re.compile(r"品牌传播"),
    "活动传播": re.compile(r"活动传播"),
    "综合定位": re.compile(r"综合.{0,4}定位"),
    "经验总结": re.compile(r"经验总结"),
}
BRAND_SECTION_PATTERNS = {
    "1.1传播概况": re.compile(r"1\.1.{0,12}传播概况"),
    "1.2口碑分析": re.compile(r"1\.2.{0,12}口碑分析"),
    "1.3内容分析": re.compile(r"1\.3.{0,12}内容分析"),
    "1.4热门内容": re.compile(r"1\.4.{0,12}热门内容"),
}
MIN_BODY_CHARS = 200  # 内容层过短（降级空壳）记 P1。
RAW_JSON_PATTERN = re.compile(r"[\{\[]\s*\"[^\"\n]+\"\s*:")
INTERNAL_FIELD_NAMES = (
    "_annot_id", "_dedup_key", "_source_analysis_object",
    "ai_sentiment", "interaction_cnt", "discussion_dimension", "情感(AI)",
    "kol_level", "Producer", "Validator", "scan_report.py",
)
PLACEHOLDER_PATTERNS = (
    "[待补充]", "TODO", "TBD", "<待补充>", "待填", "占位符",
    "（事实/机制/建议分开，不越过数据边界。）",
    "以下结论仅基于已确认统计产物与证据链。",
    "补齐证据后再制定可验证的行动计划。",
)
CAUSALITY_PATTERNS = (
    re.compile(r"活动(?:直接)?(?:带来|导致|驱动|促成).{0,24}(?:增长|提升|下降)"),
    re.compile(r"归功于本次活动"),
)
PHONE_PATTERN = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
IMAGE_PATTERN = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
SMALL_SAMPLE_DISCLOSURE_PATTERN = (
    r"(?:样本(?:量|数)?(?:不足|少于|低于)"
    r"|样本(?:量|数)?\s*(?:<|＜)\s*30)"
)


def _issue(iid: str, severity: str, title: str, evidence: str, locus: str = "") -> dict:
    return {"id": iid, "severity": severity, "title": title,
            "evidence": evidence[:300], "locus": locus}


def scan_markdown(
    markdown: str, *, degraded: bool = False,
    expected_campaigns: Optional[list[str]] = None,
    requirement: Optional[dict] = None,
    stats: Optional[dict] = None,
    charts: Optional[dict] = None,
) -> dict:
    """全量扫描内容层 MD，产出分级 issues 与 qc_report。"""
    issues: list[dict] = []
    md = markdown or ""
    lines = md.splitlines()

    # 1) 禁用措辞（P0）——逐行定位，给行号证据。
    for ln_no, line in enumerate(lines, 1):
        for term in FORBIDDEN_TERMS:
            if term in line:
                issues.append(_issue(
                    f"P0-forbidden-{term}", "P0", f"出现禁用措辞「{term}」",
                    f"L{ln_no}: {line.strip()}", locus=f"L{ln_no}"))

    # 2) 多活动相加/合计声量（P0）——同行含“不相加/逐个”等免责标记则视为口径声明，跳过。
    for ln_no, line in enumerate(lines, 1):
        if any(mk in line for mk in AGGREGATE_DISCLAIMER_MARKERS):
            continue
        for pat in AGGREGATE_SUM_PATTERNS:
            if pat in line:
                issues.append(_issue(
                    f"P0-aggregate-{pat}", "P0", f"疑似多活动相加口径「{pat}」",
                    f"L{ln_no}: {line.strip()}", locus=f"L{ln_no}"))

    # 3) 内部 JSON / 标注字段泄漏（P0）。
    for ln_no, line in enumerate(lines, 1):
        leaked_fields = [name for name in INTERNAL_FIELD_NAMES if name in line]
        if RAW_JSON_PATTERN.search(line) or leaked_fields:
            evidence = (
                f"内部字段: {', '.join(leaked_fields)}；" if leaked_fields else ""
            )
            issues.append(_issue(
                f"P0-raw-json-{ln_no}", "P0",
                "报告正文泄漏内部 JSON 或标注字段",
                f"{evidence}L{ln_no}: {line.strip()}",
                locus=f"L{ln_no}",
            ))

    # 4) 占位符、错误因果与明显隐私泄漏（P0）。
    for ln_no, line in enumerate(lines, 1):
        placeholders = [value for value in PLACEHOLDER_PATTERNS if value in line]
        if placeholders:
            issues.append(_issue(
                f"P0-placeholder-{ln_no}", "P0", "报告残留占位符",
                f"L{ln_no}: {line.strip()}", locus=f"L{ln_no}",
            ))
        if any(pattern.search(line) for pattern in CAUSALITY_PATTERNS):
            issues.append(_issue(
                f"P0-causality-{ln_no}", "P0", "活动效果使用无依据因果表述",
                f"L{ln_no}: {line.strip()}", locus=f"L{ln_no}",
            ))
        if PHONE_PATTERN.search(line):
            issues.append(_issue(
                f"P0-privacy-phone-{ln_no}", "P0", "报告泄漏手机号",
                f"L{ln_no}: {line.strip()}", locus=f"L{ln_no}",
            ))
        if re.search(r"\bUGC\b.*(?:作者|账号|昵称)[：:]", line):
            issues.append(_issue(
                f"P0-privacy-ugc-{ln_no}", "P0", "UGC 个人账号未匿名",
                f"L{ln_no}: {line.strip()}", locus=f"L{ln_no}",
            ))
        if re.search(r"确定性模板生成|结构[：:]\s*five|报告（v\d", line):
            issues.append(_issue(
                f"P0-internal-template-{ln_no}", "P0", "报告暴露内部模板或版本",
                f"L{ln_no}: {line.strip()}", locus=f"L{ln_no}",
            ))

    # 5) 报告为空 / 缺核心章节（P0）。
    body = md.strip()
    if not body:
        issues.append(_issue("P0-empty", "P0", "报告内容为空", "markdown 为空字符串"))
    else:
        for name, pat in CORE_CHAPTER_PATTERNS.items():
            if not pat.search(md):
                issues.append(_issue(
                    f"P0-missing-chapter-{name}", "P0", f"缺核心章节「{name}」",
                    f"未在标题或正文中检出「{name}」（容忍变体）", locus="structure"))
        for name, pat in BRAND_SECTION_PATTERNS.items():
            if not pat.search(md):
                issues.append(_issue(
                    f"P0-missing-brand-section-{name}", "P0",
                    f"缺品牌子章节「{name}」",
                    f"未检出完整品牌 1.1-1.4 结构中的「{name}」",
                    locus="structure",
                ))
        campaign_ids = list(expected_campaigns or [])
        if not campaign_ids:
            campaign_ids = [
                match.group(2).strip()
                for match in re.finditer(
                    r"^#{2,5}\s+2\.(\d+)\s+活动\s+(.+?)\s*$", md, flags=re.M
                )
            ]
        if not campaign_ids:
            issues.append(_issue(
                "P0-missing-campaign-sections", "P0",
                "缺逐活动 2.N 分节",
                "未检出任何「2.N 活动 <活动名>」标题",
                locus="structure",
            ))
        for index, campaign_id in enumerate(campaign_ids, 1):
            if campaign_id and campaign_id not in md:
                issues.append(_issue(
                    f"P0-missing-campaign-{index}", "P0",
                    f"缺活动分节「{campaign_id}」",
                    f"正文未检出活动名称「{campaign_id}」",
                    locus="structure",
                ))
            for suffix, title in (
                ("1", "传播概况"), ("2", "活动声量渗透分析"),
                ("3", "口碑分析"), ("4", "内容分析"), ("5", "热门内容"),
            ):
                pattern = re.compile(
                    rf"2\.{index}\.{suffix}.{{0,16}}{re.escape(title)}"
                )
                if not pattern.search(md):
                    issues.append(_issue(
                        f"P0-missing-campaign-section-{index}-{suffix}",
                        "P0",
                        f"缺活动子章节「2.{index}.{suffix} {title}」",
                        f"活动「{campaign_id}」缺固定结构",
                        locus="structure",
                    ))

    # 6) 需求一致性与当前项目适配规则。
    requirement = requirement or {}
    stats = stats or {}
    charts = charts or {}
    content_scope = str(requirement.get("content_scope") or "all").lower()
    if content_scope != "all" and re.search(r"PGC\s*[/与和对比]+\s*UGC|PGC/UGC", md):
        issues.append(_issue(
            "P0-scope-pgc-ugc", "P0", "报告超出 content_scope 输出 PGC/UGC 对比",
            f"content_scope={content_scope}", locus="requirement",
        ))
    if requirement.get("product_query"):
        has_funnel = bool(
            re.search(r"三级.{0,8}漏斗|Tier\s*1.{0,80}Tier\s*2.{0,80}Tier\s*3", md, re.S)
            or (
                "品牌提及率" in md
                and "产品提及占比" in md
                and re.search(r"活动相关(?:内容|条数|声量)", md)
            )
        )
        if not has_funnel:
            issues.append(_issue(
                "P0-product-funnel", "P0", "有产品关键词但缺递进提及漏斗",
                "要求至少呈现活动相关→品牌提及→产品提及的递进关系",
                locus="2.N.2",
            ))
    if re.search(r"竞品\s*(?:SOV|分析|声量)", md, re.I):
        issues.append(_issue(
            "P0-competitor-retired", "P0", "报告包含已下线的竞品分析",
            "当前 Topic5 权威口径已下线 competitor_sov", locus="3.2",
        ))
    if requirement or stats:
        if not re.search(r"3\.1.{0,20}(?:基准|活动期)", md):
            issues.append(_issue(
                "P0-missing-baseline-section", "P0", "缺少 3.1 活动期与基准期对比",
                "基准数据不可用时也必须保留 3.1 并披露 DataIssue", locus="3.1",
            ))
        for title in ("成功经验", "待改进问题", "行动计划"):
            if title not in md:
                issues.append(_issue(
                    f"P0-missing-summary-{title}", "P0",
                    f"经验总结缺少「{title}」",
                    "四章必须包含成功经验、待改进问题和行动计划", locus="4",
                ))
    if "整体洞察" in md and "品牌传播" in md:
        if md.find("整体洞察") > md.find("品牌传播"):
            issues.append(_issue(
                "P0-overall-insight-position", "P0", "整体洞察位置错误",
                "整体洞察必须位于标题之后、第一章之前", locus="structure",
            ))

    # 7) 统计产物可确定验证的口径。
    campaigns = stats.get("campaigns") if isinstance(stats, dict) else {}
    for cid, bundle in (campaigns or {}).items():
        metrics = (bundle or {}).get("metrics") or {}
        product_share = metrics.get("产品提及占比")
        if isinstance(product_share, (int, float)) and not 0 <= product_share <= 1:
            issues.append(_issue(
                f"P0-funnel-range-{cid}", "P0", "产品提及占比超出 0~100%",
                f"{cid}: 产品提及占比={product_share}", locus="stats_bundle",
            ))
        for dimension, values in ((bundle or {}).get("dimension_nsr") or {}).items():
            if not isinstance(values, dict):
                continue
            sample = values.get("sample_size", values.get("sample"))
            if isinstance(sample, int) and sample < 30 and str(dimension) in md:
                dimension_text = re.escape(str(dimension))
                disclosed = re.search(
                    (
                        rf"{dimension_text}.{{0,120}}{SMALL_SAMPLE_DISCLOSURE_PATTERN}"
                        rf"|{SMALL_SAMPLE_DISCLOSURE_PATTERN}.{{0,120}}{dimension_text}"
                    ),
                    md,
                    re.S,
                )
                if not disclosed:
                    issues.append(_issue(
                        f"P0-small-sample-{cid}-{dimension}", "P0",
                        "分维 NSR 小样本未披露",
                        f"{cid}/{dimension}: sample={sample}<30",
                        locus="dimension_nsr",
                    ))

    # 8) 图表 Artifact 完整性；内容层允许受控本地 PNG，发布后再检查 media。
    chart_items = charts.get("charts") if isinstance(charts, dict) else []
    for item in chart_items or []:
        if not isinstance(item, dict):
            continue
        source = str(item.get("local_path") or item.get("url") or "")
        if not source:
            issues.append(_issue(
                f"P0-chart-missing-{item.get('chart_id', 'unknown')}", "P0",
                "启用图表缺少可交付文件",
                str(item.get("title") or item.get("chart_id") or "unknown"),
                locus="chart_urls",
            ))
        elif item.get("local_path") and not Path(source).is_file():
            issues.append(_issue(
                f"P0-chart-file-{item.get('chart_id', 'unknown')}", "P0",
                "图表本地文件不存在",
                source, locus="chart_urls",
            ))
        if source and source not in IMAGE_PATTERN.findall(md):
            issues.append(_issue(
                f"P0-chart-unreferenced-{item.get('chart_id', 'unknown')}", "P0",
                "已启用图表未进入报告",
                str(item.get("title") or item.get("chart_id") or "unknown"),
                locus="report_md",
            ))

    # 9) 内容过短（P1）。
    if body and len(body) < MIN_BODY_CHARS:
        issues.append(_issue(
            "P1-too-short", "P1", "内容层偏短（疑似降级空壳）",
            f"正文仅 {len(body)} 字，低于 {MIN_BODY_CHARS}", locus="length"))

    # 10) 图表引用缺失（P1）——五章报告通常应含图/封面。
    if body and "![" not in md and "http" not in md:
        issues.append(_issue(
            "P1-no-chart", "P1", "报告未引用任何图表/封面链接",
            "正文中未检出 ![](...) 或图表 URL", locus="charts"))

    # 11) 降级标注（P1）——来源降级时留痕，便于人工复核但不拦截。
    if degraded:
        issues.append(_issue(
            "P1-degraded-source", "P1", "报告来源含降级产物",
            "report_md.degraded=True 或上游降级", locus="provenance"))

    p0 = [i for i in issues if i["severity"] == "P0"]
    p1 = [i for i in issues if i["severity"] == "P1"]
    p2 = [i for i in issues if i["severity"] == "P2"]
    return {
        "has_p0": bool(p0),
        "p0_count": len(p0),
        "p1_count": len(p1),
        "p2_count": len(p2),
        "issues": issues,
        "metric_whitelist": list(METRIC_WHITELIST),
        "forbidden_terms": list(FORBIDDEN_TERMS),
        "scan_mode": "full_scan",
        "degraded": bool(degraded),
        "rules_version": "topic5-validator-qc-v1.6",
    }


def _load_markdown(args) -> tuple[str, bool]:
    if args.markdown_file:
        return Path(args.markdown_file).read_text(encoding="utf-8"), False
    data = json.loads(Path(args.report_md).read_text(encoding="utf-8"))
    md = data.get("markdown") if isinstance(data, dict) else str(data)
    return md or "", bool(isinstance(data, dict) and data.get("degraded"))


def _input_ref(path_value: str, key: str) -> dict:
    path = Path(path_value)
    raw = path.read_bytes()
    version = None
    if path.name == "payload.json" and path.parent.name.startswith("v"):
        try:
            version = int(path.parent.name[1:])
        except ValueError:
            version = None
    return {
        "artifact_id": f"{key}@v{version}" if version is not None else key,
        "key": key,
        "version": version,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report-md", help="report_md artifact payload.json 路径")
    ap.add_argument("--markdown-file", help="纯 .md 文件路径（二选一）")
    ap.add_argument("--out", required=True)
    ap.add_argument(
        "--execution-source",
        default=os.environ.get("MA_SESSION_ID", ""),
        help="独立 Validator 子 Agent 的平台 Session/Execution ID（必填）",
    )
    ap.add_argument("--requirement", required=True)
    ap.add_argument("--stats", required=True)
    ap.add_argument("--charts", required=True)
    ap.add_argument("--rules", required=True)
    ap.add_argument("--task-id", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--attempt-id", required=True)
    ap.add_argument(
        "--validator-version",
        default=os.environ.get("TOPIC5_VALIDATOR_VERSION", "topic5-validator@1.6"),
    )
    args = ap.parse_args()
    if not (args.report_md or args.markdown_file):
        ap.error("需提供 --report-md 或 --markdown-file")
    if not args.execution_source:
        ap.error("需提供 --execution-source 绑定独立 Validator 执行来源")
    md, degraded = _load_markdown(args)
    requirement_payload = json.loads(Path(args.requirement).read_text(encoding="utf-8"))
    stats_payload = json.loads(Path(args.stats).read_text(encoding="utf-8"))
    charts_payload = json.loads(Path(args.charts).read_text(encoding="utf-8"))
    expected_campaigns = list((stats_payload.get("campaigns") or {}).keys())
    qc = scan_markdown(
        md,
        degraded=degraded,
        expected_campaigns=expected_campaigns,
        requirement=requirement_payload,
        stats=stats_payload,
        charts=charts_payload,
    )
    subject = None
    if args.report_md:
        report_path = Path(args.report_md)
        try:
            version = int(report_path.parent.name.lstrip("v"))
            subject = {
                "artifact_id": f"report_md@v{version}",
                "key": "report_md",
                "version": version,
                "sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
            }
        except (TypeError, ValueError):
            ap.error("--report-md 路径必须位于 artifacts/report_md/vN/payload.json")
    qc.update({
        "validator": "topic5-validator",
        "validation_id": f"validation_{uuid.uuid4().hex}",
        "subject": subject,
        "rules_version": "topic5-validator-qc-v1.6",
        "execution_source": args.execution_source,
        "validator_version": args.validator_version,
        "execution_ref": {
            "task_id": args.task_id,
            "run_id": args.run_id,
            "attempt_id": args.attempt_id,
        },
        "checked_at": int(time.time()),
        "checkable_scope": [
            "forbidden_terms", "multi_activity_sum", "core_chapters",
            "raw_json_leakage", "body_length", "chart_references",
            "degraded_provenance", "requirement_scope", "product_funnel",
            "competitor_retired", "placeholder_leakage", "causality",
            "phone_privacy", "small_sample_disclosure", "chart_files",
        ],
        "uncheckable_scope": [
            "semantic_evidence", "live_feishu_layout", "document_ownership",
            "external_link_access",
        ],
        "qc_metadata": {
            "actor": "topic5-validator",
            "mode": "independent-agent",
            "validator_id": "topic5-validator",
            "generated_at": int(time.time()),
            "input_artifacts": [
                item for item in (
                    subject,
                    _input_ref(args.requirement, "requirement_confirmed"),
                    _input_ref(args.stats, "stats_bundle"),
                    _input_ref(args.charts, "chart_urls"),
                    _input_ref(args.rules, "validator_rules"),
                ) if item
            ],
        },
    })
    Path(args.out).write_text(json.dumps(qc, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    print(f"[validator] p0={qc['p0_count']} p1={qc['p1_count']} "
          f"has_p0={qc['has_p0']} -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
