"""Report language policy shared by generation and independent validation.

Metric naming is deterministic. Whether a price/value judgement has evidence is
a semantic rule, not a substring rule. Source text is exempt only when matched
to the statistics used to assemble the report; Markdown quoting alone is not
proof of provenance.
"""
from __future__ import annotations

import re

METRIC_TERMS = ("人均", "转化率", "转化")
SEMANTIC_TERMS = ("性价比",)


def _excerpt(row):
    return re.sub(r"\s+", " ", str(row.get("content") or row.get("title") or ""))[:100]


def _cell(text):
    return str(text).replace("\\", "\\\\").replace("|", r"\|").replace("\n", " ").replace("\r", " ")


def _sources(bundle):
    rows = []
    labels = set()
    for kind in ("pgc", "ugc"):
        rows.extend((bundle.get("top_content") or {}).get(kind) or [])
    for direction in ("positive", "negative"):
        for topic in ((bundle.get("opinion_analysis") or {}).get(direction) or {}).get("top5") or []:
            if topic.get("label"):
                labels.add(str(topic["label"]))
            rows.extend(topic.get("representatives") or [])
    for category in (bundle.get("ugc_analysis") or {}).get("categories") or []:
        if category.get("label"):
            labels.add(str(category["label"]))
        rows.extend(category.get("representatives") or [])
    return labels, [row for row in rows if isinstance(row, dict)]


def analysis_lines(markdown, *, stats=None):
    """Yield original line numbers and text with only grounded evidence masked."""
    stats = stats or {}
    scopes = {"1.4": stats.get("brand") or {}}
    for index, bundle in enumerate((stats.get("campaigns") or {}).values(), 1):
        for suffix in ("3", "4", "5"):
            scopes[f"2.{index}.{suffix}"] = bundle
    sources = {number: _sources(bundle) for number, bundle in scopes.items()}
    section = None
    evidence_kind = None
    for number, line in enumerate((markdown or "").splitlines(), 1):
        heading = re.match(r"^(?:#{1,6}\s+|\*\*)(\d+(?:\.\d+)*)(?:\s|[·：])", line)
        if heading:
            section = heading.group(1)
            evidence_kind = None
        if re.match(r"\*\*(?:正面|负面)观点 TOP5\*\*", line):
            evidence_kind = "opinion"
        elif line.startswith("**UGC参与方式与代表内容**"):
            evidence_kind = "ugc"
        labels, rows = sources.get(section, (set(), []))
        spans = []
        # Labels only occupy the dedicated opinion/UGC module, not prose.
        if evidence_kind:
            for label in labels:
                prefix = f"**{label}**："
                if line.startswith(prefix):
                    spans.append((0, len(prefix) - 1))
        # Quotes must match the source excerpt, and their link if present.
        for row in rows:
            text = _excerpt(row)
            if not text:
                continue
            pattern = re.escape(f"「{text}」") + r"(?:\s*\[原文\]\(([^)]+)\))?"
            for quote in re.finditer(pattern, line):
                url = quote.group(1)
                if not url or url == str(row.get("url") or ""):
                    spans.append(quote.span(0))
        # Source title/content cells only in the dedicated Top10 tables.
        if line.startswith("|") and (section == "1.4" or str(section).endswith(".5")):
            values = {_cell(value) for row in rows
                      for value in (_excerpt(row), row.get("title") or "")
                      if value}
            for value in values:
                for cell in re.finditer(r"(?<=\|) " + re.escape(value) + r" (?=\|)", line):
                    spans.append(cell.span(0))
        masked = list(line)
        for start, end in spans:
            masked[start:end] = " " * (end - start)
        yield number, line, "".join(masked)


def metric_violations(markdown, *, stats=None):
    return [(number, term, original)
            for number, original, analysis in analysis_lines(markdown, stats=stats)
            for term in METRIC_TERMS if term in analysis]


def semantic_review(markdown, *, stats=None):
    """Locations requiring an evidence judgement by the independent Validator."""
    return [{"term": term, "locus": f"L{number}", "evidence": original.strip()}
            for number, original, analysis in analysis_lines(markdown, stats=stats)
            for term in SEMANTIC_TERMS if term in analysis]
