"""Deterministic evidence and required report modules; no model or external calls."""
from __future__ import annotations

import re
from collections import defaultdict

PLATFORMS = {"xiaohongshu": "小红书", "douyin": "抖音", "weibo": "微博",
             "bilibili": "哔哩哔哩", "weixin": "微信"}
PGC_COLUMNS = ["排名", "标题", "内容≤100字", "内容类型", "站点名称", "作者", "URL", "总互动量"]
UGC_COLUMNS = ["排名", "内容≤100字", "站点名称", "URL", "总互动量"]


def excerpt(row, limit=100):
    return re.sub(r"\s+", " ", str(row.get("content") or row.get("title") or ""))[:limit]


def public_row(row):
    """Public evidence only. Personal UGC account metadata never enters report input."""
    pgc = str(row.get("content_mode") or "").upper() == "PGC"
    result = {k: row.get(k) for k in ("id", "url", "title", "content_mode", "interaction_cnt")}
    result["content"] = excerpt(row)
    result["site_name"] = row.get("site_name") or PLATFORMS.get(row.get("_platform"), row.get("_platform"))
    if pgc:
        result["author"] = row.get("author") or row.get("nickname") or row.get("account_name")
    return result


def _rank(rows):
    return sorted(rows, key=lambda r: (-int(r.get("interaction_cnt") or 0),
                                     str(r.get("id") or r.get("url") or excerpt(r))))


def top_content(rows):
    return {kind.lower(): [public_row(r) for r in _rank(rows)
                          if str(r.get("content_mode") or "").upper() == kind][:10]
            for kind in ("PGC", "UGC")}


def opinion_analysis(rows, sentiment):
    result = {}
    for name, value in (("positive", 1), ("negative", -1)):
        subset = [r for r in rows if sentiment(r) == value]
        groups = defaultdict(list)
        for row in subset:
            label = str(row.get("opinion") or "").strip()
            if label and label != "N/A":
                groups[label].append(row)
        result[name] = {
            "sample_size": len(subset), "labelled_count": sum(map(len, groups.values())),
            "status": "available" if len(subset) >= 10 else "insufficient_sample",
            "top5": [{"label": label, "count": len(group),
                      "share": len(group) / len(subset),
                      "representatives": [public_row(r) for r in _rank(group)[:3]]}
                     for label, group in sorted(groups.items(),
                                                key=lambda item: (-len(item[1]), item[0]))[:5]],
        }
    return result


def ugc_analysis(rows):
    subset = [r for r in rows if str(r.get("content_mode") or "").upper() == "UGC"]
    groups = defaultdict(list)
    for row in subset:
        label = str(row.get("ugc_category") or "").strip()
        if label and label != "N/A":
            groups[label].append(row)
    return {"sample_size": len(subset),
            "categories": [{"label": label, "count": len(group), "share": len(group) / len(subset),
                            "representatives": [public_row(r) for r in _rank(group)[:2]]}
                           for label, group in sorted(groups.items(),
                                                      key=lambda item: (-len(item[1]), item[0]))]}


def cell(value):
    return str(value if value is not None else "").replace("\\", "\\\\").replace("|", r"\|").replace("\n", " ").replace("\r", " ")


def table(columns, rows):
    return "\n".join(["| " + " | ".join(columns) + " |",
                      "| " + " | ".join(["---"] * len(columns)) + " |"]
                     + ["| " + " | ".join(cell(v) for v in row) + " |" for row in rows])


def hot_tables(bundle, scope=""):
    parts = []
    for kind, columns in (("pgc", PGC_COLUMNS), ("ugc", UGC_COLUMNS)):
        rows = (bundle.get("top_content") or {}).get(kind) or []
        parts.append(f"**{scope + ' ' if scope else ''}{kind.upper()} 热门内容 Top10**")
        if not rows:
            parts.append(f"本次可用数据中无 {kind.upper()} 内容。")
            continue
        values = []
        for rank, row in enumerate(rows[:10], 1):
            common = [rank, excerpt(row), row.get("site_name"), row.get("url"), row.get("interaction_cnt")]
            if kind == "pgc":
                common = [rank, row.get("title"), excerpt(row), row.get("content_mode") or "PGC",
                          row.get("site_name"), row.get("author"), row.get("url"), row.get("interaction_cnt")]
            values.append(common)
        parts.append(table(columns, values))
    return "\n\n".join(parts)


def opinion_text(bundle):
    parts = []
    for name, title in (("positive", "正面"), ("negative", "负面")):
        item = (bundle.get("opinion_analysis") or {}).get(name) or {}
        sample = item.get("sample_size", 0)
        parts.append(f"**{title}观点 TOP5**")
        parts.append(f"{title}样本 {sample} 条；观点占比的分母为该方向全部内容。")
        if sample < 10:
            parts.append("样本不足10条，仅作方向性参考。")
        if not item.get("top5"):
            parts.append("可用样本中未提取到有效观点标签。")
        for topic in item.get("top5") or []:
            parts.append(f"**{topic['label']}**：{topic['count']} 条（{topic['share']:.1%}）")
            for row in topic.get("representatives") or []:
                parts.append(f"> 「{excerpt(row)}」 [原文]({row.get('url') or ''})")
    return "\n\n".join(parts)


def ugc_text(bundle):
    item = bundle.get("ugc_analysis") or {}
    parts = [f"**UGC参与方式与代表内容**\n\nUGC样本 {item.get('sample_size', 0)} 条；占比以全部UGC为分母。"]
    for category in item.get("categories") or []:
        parts.append(f"**{category['label']}**：{category['count']} 条（{category['share']:.1%}）")
        for row in category.get("representatives") or []:
            parts.append(f"> 「{excerpt(row)}」 [原文]({row.get('url') or ''})")
    return "\n\n".join(parts)


def _headings(markdown):
    """Read headings and numbered bold titles with their semantic hierarchy."""
    pattern = r"(?m)^(?:(#{1,6})\s+([^\n]+)|\*\*(\d+(?:\.\d+)*\s+[^\n]+?)\*\*\s*)$"
    result = []
    for match in re.finditer(pattern, markdown):
        title = (match.group(2) or match.group(3)).strip().strip("*").strip()
        number = re.match(r"^(\d+(?:\.\d+)*)(?=\s|$)", title)
        level = number.group(1).count(".") + 2 if number else len(match.group(1))
        result.append((match.start(), match.end(), level, title))
    return result


def section(markdown, prefix):
    headings = _headings(markdown)
    for index, (start, end, level, title) in enumerate(headings):
        if not re.match(re.escape(prefix) + r"(?=[^\d.]|$)", title):
            continue
        stop = next((item[0] for item in headings[index + 1:] if item[2] <= level),
                    len(markdown))
        return start, end, stop
    return None


def narrative_heading_keys(markdown):
    """Ignore generated evidence modules; numbered sections have stable IDs."""
    text = re.sub(r"<!-- report-module:start -->.*?<!-- report-module:end -->",
                  "", markdown, flags=re.S)
    keys = set()
    for _, _, level, title in _headings(text):
        if level < 2:
            continue
        number = re.match(r"^(\d+(?:\.\d+)*)(?=\s|$)", title)
        keys.add("section:" + number.group(1) if number else
                 "title:" + re.sub(r"[\s*]", "", title))
    return keys


def assert_preserved_structure(previous, revised):
    missing = narrative_heading_keys(previous) - narrative_heading_keys(revised)
    # Unnumbered titles may legitimately switch from headings to bold labels.
    compact = re.sub(r"[\s*#]", "", revised)
    missing = {key for key in missing
               if not (key.startswith("title:") and key[6:] in compact)}
    if missing:
        raise ValueError("修订丢失原有章节: " + ", ".join(sorted(missing)))


def _table_rows(markdown):
    tables, rows = [], []
    for line in markdown.splitlines() + [""]:
        line = line.strip()
        if line.startswith("|") and line.endswith("|"):
            rows.append([part.strip() for part in re.split(r"(?<!\\)\|", line)[1:-1]])
        elif rows:
            tables.append(rows)
            rows = []
    return tables


def assert_hot_tables(markdown, stats):
    """Check actual columns, rows and source values, regardless of title style."""
    bundles = [("1.4", stats.get("brand") or {})]
    bundles += [(f"2.{index}.5", bundle)
                for index, bundle in enumerate((stats.get("campaigns") or {}).values(), 1)]
    for prefix, bundle in bundles:
        bounds = section(markdown, prefix)
        if not bounds:
            raise ValueError(f"热门表缺所属章节: {prefix}")
        body = markdown[bounds[1]:bounds[2]]
        if _table_rows(body) != _table_rows(hot_tables(bundle)):
            raise ValueError(f"热门表字段、行数或源数据不一致: {prefix}")
        for kind in ("pgc", "ugc"):
            if not (bundle.get("top_content") or {}).get(kind):
                if f"本次可用数据中无 {kind.upper()} 内容。" not in body:
                    raise ValueError(f"热门表缺无数据披露: {prefix}/{kind}")


def _replace(markdown, prefix, body, append=False):
    bounds = section(markdown, prefix)
    if not bounds:
        raise ValueError(f"报告缺必需章节: {prefix}")
    start, heading_end, stop = bounds
    previous = markdown[heading_end:stop].strip() if append else ""
    return markdown[:heading_end] + "\n\n" + (previous + "\n\n" if previous else "") + body + "\n\n" + markdown[stop:]


def data_description(plan, stats):
    platforms = "、".join(PLATFORMS.get(p, p) for p in plan.get("platforms") or [])
    sampling = (stats.get("clean_record") or {}).get("sampling") or {}
    text = (f"## 00 篇头 · 数据说明\n\n分析平台：{platforms}。"
            f"分析周期：{plan.get('period_start')} 至 {plan.get('period_end')}。\n\n"
            "品牌大盘采用监测平台聚合数据；活动表现采用用户上传的活动明细，经周期过滤、去重、相关性与情感校验。"
            "两类数据分析对象和口径不同，不直接相加。\n\n")
    if sampling.get("applied"):
        text += (f"demo抽样：清洗后总体 {sampling.get('population_size')} 条，实际分析样本 "
                 f"{sampling.get('sample_size')} 条；仅供参考、不代表全量结论。\n\n")
    else:
        record = stats.get("clean_record") or {}
        text += f"活动明细：导入 {record.get('total_in', '未记录')} 条，清洗后分析 {record.get('total_out', '未记录')} 条，无抽样。\n\n"
    rows = [["品牌大盘", plan.get("brand_query") or plan.get("brand"), plan.get("brand_filter")],
            ["活动", plan.get("activity_query") or plan.get("activity_name"), plan.get("activity_filter")],
            ["产品", plan.get("product_query"), ""]]
    for campaign in plan.get("campaigns") or []:
        query = campaign.get("activity_query") or campaign.get("query") or "|".join(campaign.get("filter_words") or [])
        if query:
            rows.append([f"活动：{campaign.get('id')}", query, plan.get("activity_filter")])
    return text + table(["分析对象", "关键词", "过滤词"], rows)


def assemble(markdown, *, plan, stats, charts, insight):
    """Bind required tables, citations and charts to their actual analysis object."""
    md = re.sub(r"<!-- report-module:start -->.*?<!-- report-module:end -->",
                "", markdown, flags=re.S)
    # Emit standard numbered headings so the independent scanner and Feishu
    # converter receive the same canonical structure as the repair validator.
    for start, end, level, title in reversed(_headings(md)):
        if md[start:end].startswith("**"):
            md = md[:start] + "#" * level + " " + title + md[end:]
    def module(text):
        return "<!-- report-module:start -->\n" + text + "\n<!-- report-module:end -->"
    # Replace only the description area; retain the model's overall insight.
    description = re.search(r"(?m)^#{1,6}\s+[^\n]*数据说明[^\n]*", md)
    if description:
        # A combined intro has no reliable split between notes and narrative.
        # Retain its body under the insight title rather than guessing a range.
        if "整体洞察" in description.group():
            md = (md[:description.start()] + data_description(plan, stats)
                  + "\n\n## 整体洞察" + md[description.end():])
            description = re.search(r"(?m)^## 00 篇头 · 数据说明[^\n]*", md)
        tail = re.search(
            r"(?m)^(?:#{1,6}\s+|\*\*(?:\d+(?:\.\d+)*\s+)?整体洞察[^\n]*\*\*)",
            md[description.end():],
        )
        stop = description.end() + tail.start() if tail else len(md)
        md = md[:description.start()] + data_description(plan, stats) + "\n\n" + md[stop:]
    else:
        title_end = md.find("\n")
        title_end = title_end if title_end >= 0 else len(md)
        md = md[:title_end] + "\n\n" + data_description(plan, stats) + "\n\n" + md[title_end:]
    md = _replace(md, "1.4", module(hot_tables(stats.get("brand") or {}, "品牌")))
    campaigns = stats.get("campaigns") or {}
    for index, (cid, bundle) in enumerate(campaigns.items(), 1):
        md = _replace(md, f"2.{index}.5", module(hot_tables(bundle, f"活动 {cid}")))
    # Reject invented local paths before rebuilding the known chart placement.
    items = charts.get("charts") or []
    paths = {item.get("local_path") for item in items if item.get("local_path")}
    cover = charts.get("cover") or {}
    if isinstance(cover, dict) and cover.get("local_path"):
        paths.add(cover["local_path"])
    for path in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", md):
        if path not in paths:
            raise ValueError("报告图片引用不属于已生成图表清单")
    def chart_order(item):
        cid = str(item.get("chart_id") or "")
        return next((i for i, suffix in enumerate(("_trend", "_platform", "_content"))
                     if cid.endswith(suffix)), 3)
    for item in sorted(items, key=chart_order):
        path = item.get("local_path")
        if not path:
            raise ValueError("必需图表缺少本地文件")
        md = re.sub(r"!\[[^\]]*\]\(" + re.escape(path) + r"\)\s*", "", md)
        cid = str(item.get("chart_id") or "")
        prefix = "1.1"
        topic_bundle = None
        if cid.startswith("brand_"):
            prefix = "1.2" if "sentiment" in cid else "1.1"
        elif cid.startswith("wordcloud_brand_"):
            prefix = "1.3"
            platform = cid[len("wordcloud_brand_overall_"):]
            topic_bundle = ((insight.get("brand") or {}).get("overall") or {}).get("platforms", {}).get(platform)
        elif cid == "baseline_lift":
            prefix = "3.1"
        else:
            for index, campaign in enumerate(campaigns, 1):
                safe = re.sub(r"[^\w\u4e00-\u9fff\-]", "_", campaign)
                if cid.startswith(f"wordcloud_{safe}_"):
                    prefix = f"2.{index}.4"
                    dimension_platform = cid[len(f"wordcloud_{safe}_"):]
                    dimension, _, platform = dimension_platform.partition("_")
                    topic_bundle = ((insight.get("campaigns") or {}).get(campaign, {}).get(dimension) or {}).get("platforms", {}).get(platform)
                    break
                if cid.startswith(campaign + "_"):
                    suffix = "2" if "mention_funnel" in cid else "3" if "sentiment" in cid or "dimension_nsr" in cid else "4" if "ugc_category" in cid or "kol_effectiveness" in cid else "1"
                    prefix = f"2.{index}.{suffix}"
                    break
        body = f"![{item.get('title') or cid}]({path})"
        if "wordcloud" in cid:
            if not topic_bundle or not topic_bundle.get("topics"):
                raise ValueError(f"词云缺少对应解读: {cid}")
            body += f"\n\n**{PLATFORMS.get(platform, platform)}核心议题解读**"
            for topic in topic_bundle["topics"]:
                frequencies = topic.get("word_frequencies") or {}
                words = "、".join(f"{word}（{frequencies[word]}）" if word in frequencies else word
                                 for word in topic.get("words") or [])
                body += f"\n\n**{topic.get('topic')}**（关键词：{words}）\n\n{topic.get('insight') or ''}\n\n{topic.get('action') or ''}"
        elif "mention_funnel" in cid:
            spec = item.get("spec") or {}
            values = ((spec.get("series") or [{}])[0].get("values") or [])
            body += "\n\n" + table(["提及层级", "条数"], list(zip(spec.get("categories") or [], values)))
        elif "sentiment" in cid and prefix.startswith("2."):
            index = int(prefix.split(".")[1]) - 1
            bundle = list(campaigns.values())[index]
            sd = bundle.get("sentiment_dist") or {}
            total = sum(sd.values())
            body += "\n\n" + table(["情感", "条数", "占比"], [[name, sd.get(key, 0), f"{sd.get(key, 0)/total:.1%}" if total else "无样本"]
                                                             for key, name in (("positive", "正面"), ("neutral", "中性"), ("negative", "负面"))])
            body += f"\n\n活动整体 NSR：{(bundle.get('metrics') or {}).get('NSR')}。分母{'包含' if bundle.get('nsr_denominator_includes_neutral') else '不包含'}中性。"
        md = _replace(md, prefix, module(body), append=True)
    for index, (_, bundle) in enumerate(campaigns.items(), 1):
        md = _replace(md, f"2.{index}.3", module(opinion_text(bundle)), append=True)
        md = _replace(md, f"2.{index}.4", module(ugc_text(bundle)), append=True)
    assert_hot_tables(md, stats)
    return md
