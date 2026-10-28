"""图表 + 封面渲染适配层 — 供 charts_render handler（n14_charts）使用。

图表只渲染成**本地 PNG**。正式交付物是飞书文档，图片通过 medias/upload_all
上传本地二进制拿 file_token 再引用（见 feishu.py）。

设计（类比 ssjh.py / semantic.py）：
  - 规格构建 build_chart_specs(stats_bundle, wordcloud_data)：**纯确定性**，把统计产物
    翻译成图表 spec 列表（符合仓内 blueai-yuqing-echarts-skill-new 的 render_from_spec.js
    bundle 契约：chart_id / title / chart_type / categories / series[{name, values, unit}]）。
    此步无副作用、可单测，是真路径与降级路径共用的“图表选题 + 数据绑定”层。
  - 真路径 render_charts()：逐张调 node render_from_spec.js 渲染 PNG 到 outdir，逐张自检
    （文件存在且非空）；封面调 report-cover-skill（best-effort）。返回每张图的本地
    local_path，由飞书交付层上传 media。无 node_modules / node / 渲染失败即抛异常，由
    handler 降级。
  - 降级 fallback()：返回与真路径同构的 chart_urls（degraded=True），每张图带 spec，
    local_path=None（无渲染器时无图），供下游 write_report/飞书交付无差别消费（缺图跳过）。

凭据只从环境变量读（生产由 MA Vault 注入）：
  REPORT_COVER_API_KEY —— 缺失即跳过封面真生成（降级，不影响图表主体）。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from typing import Any, Optional

# 仓内图表 / 封面 skill 根目录
_ORCH_DIR = os.path.dirname(os.path.abspath(__file__))
_SKILLS_DIR = os.path.dirname(_ORCH_DIR)
ECHARTS_DIR = os.path.join(_SKILLS_DIR, "blueai-yuqing-echarts-skill-new")
COVER_DIR = os.path.join(_SKILLS_DIR, "report-cover-skill")
_RENDER_SCRIPT = os.path.join(ECHARTS_DIR, "scripts", "render_from_spec.js")
_ECHARTS_NODE_MODULES = os.path.join(ECHARTS_DIR, "scripts", "node_modules")

# 情感维度中文标签（与 _campaign_stats.sentiment_dist 键对齐）。
_SENTI_LABEL = {"positive": "正面", "neutral": "中性", "negative": "负面"}
# 平台 slug → 中文展示名（与 clean_campaign 的 _PLATFORM_CN 逆映射对齐）。
_PLATFORM_LABEL = {
    "xiaohongshu": "小红书", "douyin": "抖音", "bilibili": "哔哩哔哩",
    "weibo": "微博", "weixin": "微信", "unknown": "未知",
}
_CONTENT_LABEL = {"UGC": "UGC", "PGC": "PGC", "UKN": "未知"}


# ── 可用性探测 ──────────────────────────────────────────────
def renderer_available() -> bool:
    """图表真渲染条件：render 脚本在仓 + node_modules 已安装（含 echarts/resvg）+ node 可执行。

    node_modules 不入库（.gitignore），仅在 MA Linux 沙箱安装；本地测试环境缺它即降级。
    """
    if not (os.path.isfile(_RENDER_SCRIPT) and os.path.isdir(_ECHARTS_NODE_MODULES)):
        return False
    try:
        subprocess.run(["node", "--version"], capture_output=True, timeout=10, check=True)
        return True
    except Exception:
        return False


def cover_available() -> bool:
    """封面真生成条件：有 relay key 且封面脚本在仓。"""
    return bool(os.environ.get("REPORT_COVER_API_KEY")) and \
        os.path.isfile(os.path.join(COVER_DIR, "scripts", "generate_cover_gpt.py"))


# ── 图表选题 + 数据绑定（纯确定性，真/降级共用）──────────────
def _series(name: str, values: list, unit: Optional[str] = None) -> dict:
    s = {"name": name, "values": values}
    if unit:
        s["unit"] = unit
    return s


def _sentiment_spec(cid: str, stats: dict) -> Optional[dict]:
    """情感分布环形图（正/中/负）。全零不出图。"""
    sd = (stats or {}).get("sentiment_dist") or {}
    cats, vals = [], []
    for k in ("positive", "neutral", "negative"):
        cats.append(_SENTI_LABEL[k])
        vals.append(int(sd.get(k) or 0))
    if sum(vals) == 0:
        return None
    return {"chart_id": f"{cid}_sentiment", "title": f"活动 {cid}·情感分布",
            "chart_type": "donut", "metric": "声量占比", "dimension": "情感",
            "categories": cats, "series": [_series("声量", vals)],
            "source_mode": "native"}


def _platform_spec(cid: str, stats: dict) -> Optional[dict]:
    """平台声量分布环形图。少于 2 个平台不出图。"""
    pd = (stats or {}).get("platform_dist") or {}
    items = sorted(pd.items(), key=lambda kv: kv[1], reverse=True)
    items = [(p, v) for p, v in items if v > 0]
    if len(items) < 2:
        return None
    cats = [_PLATFORM_LABEL.get(p, p) for p, _ in items]
    vals = [int(v) for _, v in items]
    return {"chart_id": f"{cid}_platform", "title": f"活动 {cid}·平台声量分布",
            "chart_type": "donut", "metric": "声量", "dimension": "平台",
            "categories": cats, "series": [_series("声量", vals)],
            "source_mode": "native"}


def _trend_spec(cid: str, stats: dict) -> Optional[dict]:
    """日声量+互动量双序列趋势。"""
    tr = (stats or {}).get("trend") or {}
    interaction = (stats or {}).get("interaction_trend") or {}
    pts = sorted(tr.items())
    if len(pts) < 2:
        return None
    cats = [k for k, _ in pts]
    vals = [int(v) for _, v in pts]
    return {"chart_id": f"{cid}_trend", "title": f"活动 {cid}·日声量趋势",
            "chart_type": "line", "metric": "声量", "dimension": "日期",
            "categories": cats, "series": [
                _series("日声量", vals, "条"),
                _series("日互动量", [int(interaction.get(key) or 0) for key in cats]),
            ],
            "source_mode": "native"}


def _content_spec(cid: str, stats: dict) -> Optional[dict]:
    """内容类型分布环形图（UGC/PGC/未知）。全零或单一类型不出图。"""
    cd = (stats or {}).get("content_type_dist") or {}
    cats, vals = [], []
    for k in ("UGC", "PGC", "UKN"):
        v = int(cd.get(k) or 0)
        if v > 0:
            cats.append(_CONTENT_LABEL[k])
            vals.append(v)
    if len(cats) < 2:
        return None
    return {"chart_id": f"{cid}_content", "title": f"活动 {cid}·内容类型分布",
            "chart_type": "donut", "metric": "声量占比", "dimension": "内容类型",
            "categories": cats, "series": [_series("声量", vals)],
            "source_mode": "native"}


def _analysis_specs(cid: str, stats: dict) -> list[dict]:
    specs = []
    funnel = (stats or {}).get("mention_funnel") or {}
    if funnel.get("brand_hits") is not None:
        cats = ["活动相关", "品牌提及"]
        vals = [int((stats.get("metrics") or {}).get("活动相关条数") or 0),
                int(funnel.get("brand_hits") or 0)]
        if funnel.get("has_product_keywords"):
            cats.append("产品提及")
            vals.append(int(funnel.get("product_hits") or 0))
        specs.append({
            "chart_id": f"{cid}_mention_funnel",
            "title": f"活动 {cid}·提及漏斗",
            "chart_type": "bar",
            "categories": cats,
            "series": [_series("声量", vals)],
            "source_mode": "native",
        })
    dimensions = [
        (name, item.get("value"))
        for name, item in ((stats or {}).get("dimension_nsr") or {}).items()
        if item.get("status") == "available" and item.get("value") is not None
    ]
    if dimensions:
        specs.append({
            "chart_id": f"{cid}_dimension_nsr",
            "title": f"活动 {cid}·分维度 NSR",
            "chart_type": "rank_bar",
            "categories": [name for name, _ in dimensions],
            "series": [_series("NSR", [value for _, value in dimensions])],
            "source_mode": "native",
        })
    kol = (stats or {}).get("kol_effectiveness") or {}
    levels = [level for level in ("head", "waist", "tail") if level in kol]
    if levels:
        specs.append({
            "chart_id": f"{cid}_kol_effectiveness",
            "title": f"活动 {cid}·达人层级效能",
            "chart_type": "bar",
            "categories": levels,
            "series": [
                _series("声量占比", [kol[level]["volume_share"] for level in levels]),
                _series("条均互动量", [kol[level]["avg_interaction"] for level in levels]),
            ],
            "source_mode": "native",
        })
    ugc = (stats or {}).get("ugc_category_dist") or {}
    if ugc:
        specs.append({
            "chart_id": f"{cid}_ugc_category",
            "title": f"活动 {cid}·UGC 参与方式",
            "chart_type": "donut",
            "categories": list(ugc),
            "series": [_series("声量", [ugc[key] for key in ugc])],
            "source_mode": "native",
        })
    return specs


def _wordcloud_specs(wordcloud_data: dict) -> list[dict]:
    """逐活动、维度、平台生成真实词云规格。"""
    specs = []
    for cid, dimensions in ((wordcloud_data or {}).get("campaigns") or {}).items():
        for dimension, bundle in (dimensions or {}).items():
            for plat, words in (bundle.get("platforms") or {}).items():
                if not isinstance(words, dict) or not words:
                    continue
                top = sorted(words.items(), key=lambda kv: kv[1], reverse=True)[:100]
                specs.append({
                    "chart_id": f"wordcloud_{_safe(cid)}_{dimension}_{plat}",
                    "title": f"{cid}·{dimension}·{_PLATFORM_LABEL.get(plat, plat)}词云",
                    "chart_type": "wordcloud",
                    "metric": "词频",
                    "dimension": "关键词",
                    "categories": [word for word, _ in top],
                    "series": [_series("词频", [int(freq) for _, freq in top])],
                    "source_mode": "native",
                })
    return specs


def build_chart_specs(stats_bundle: dict, wordcloud_data: dict) -> list[dict]:
    """把统计产物翻译为图表 spec 列表（确定性、无副作用、可单测）。

    资格规则（简化自 echarts skill §1/§8）：剔除空图/全零/单值/单类别；多活动逐个出图；
    派生词云图不继承“图表占位”。返回顺序稳定，供真渲染与降级占位复用。
    """
    specs: list[dict] = []
    brand = (stats_bundle or {}).get("brand") or {}
    if brand:
        sentiment = brand.get("sentiment_dist") or {}
        if isinstance(sentiment.get("_total"), dict):
            sentiment = sentiment["_total"]
        content = brand.get("content_type_dist") or {}
        if isinstance(content.get("_total"), dict):
            content = content["_total"]
        brand_stats = {
            "sentiment_dist": sentiment,
            "platform_dist": brand.get("platform_dist") or {},
            "trend": brand.get("trend") or {},
            "interaction_trend": brand.get("interaction_trend") or {},
            "content_type_dist": content,
        }
        for builder in (_sentiment_spec, _platform_spec, _trend_spec, _content_spec):
            spec = builder("brand", brand_stats)
            if spec:
                spec["title"] = spec["title"].replace("活动 brand", "品牌")
                specs.append(spec)
    camps = (stats_bundle or {}).get("campaigns") or {}
    for cid, stats in camps.items():
        for builder in (_sentiment_spec, _platform_spec, _trend_spec, _content_spec):
            spec = builder(cid, stats)
            if spec:
                specs.append(spec)
        specs.extend(_analysis_specs(cid, stats))
    lift = (stats_bundle or {}).get("baseline_lift") or {}
    if lift.get("status") == "available":
        specs.append({
            "chart_id": "baseline_lift",
            "title": "活动期与基准期声量对比",
            "chart_type": "bar",
            "categories": ["活动期", "基准期"],
            "series": [_series("声量", [
                lift.get("period_volume") or 0,
                lift.get("baseline_volume") or 0,
            ])],
            "source_mode": "native",
        })
    specs.extend(_wordcloud_specs(wordcloud_data))
    return specs


# ── 文件名安全化 ────────────────────────────────────────────
def _safe(name: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff\-]", "_", str(name or ""))


# ── 真路径：逐张渲染为本地 PNG ───────────────────────────────
def _render_png(spec: dict, out_png: str, width: int = 1200, height: int = 600) -> None:
    """调 render_from_spec.js 把单 spec 渲染为 PNG；逐张自检文件存在且非空。"""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                     encoding="utf-8") as f:
        import json as _json
        _json.dump(spec, f, ensure_ascii=False)
        spec_path = f.name
    try:
        subprocess.run(
            ["node", _RENDER_SCRIPT, "--spec", spec_path, "--output", out_png,
             "--width", str(width), "--height", str(height)],
            capture_output=True, timeout=120, check=True,
        )
    finally:
        try:
            os.unlink(spec_path)
        except OSError:
            pass
    if not (os.path.isfile(out_png) and os.path.getsize(out_png) > 0):
        raise RuntimeError(f"图表渲染产物为空: {spec.get('chart_id')}")


def _render_wordcloud(spec: dict, out_png: str) -> None:
    try:
        from wordcloud import WordCloud
    except ImportError as error:
        raise RuntimeError("缺少 wordcloud 渲染依赖") from error
    categories = spec.get("categories") or []
    values = ((spec.get("series") or [{}])[0].get("values") or [])
    frequencies = {
        str(word): float(value)
        for word, value in zip(categories, values)
        if float(value or 0) > 0
    }
    if not frequencies:
        raise RuntimeError(f"词云数据为空: {spec.get('chart_id')}")
    font_path = os.path.join(
        ECHARTS_DIR, "assets", "fonts", "SourceHanSansSC-Regular.otf"
    )
    cloud = WordCloud(
        width=1200,
        height=600,
        background_color=None,
        mode="RGBA",
        font_path=font_path if os.path.isfile(font_path) else None,
        collocations=False,
        random_state=42,
    ).generate_from_frequencies(frequencies)
    cloud.to_file(out_png)


def _gen_cover(title: str, subtitle: str, out_png: str) -> Optional[str]:
    """封面真生成（best-effort）；成功返回本地路径，任何失败返回 None。"""
    if not cover_available():
        return None
    if os.path.join(COVER_DIR, "scripts") not in sys.path:
        sys.path.insert(0, os.path.join(COVER_DIR, "scripts"))
    try:
        from generate_cover_gpt import generate_report_cover_gpt  # noqa: E402
        r = generate_report_cover_gpt(
            title=title, subtitle=subtitle, title_en=title, subtitle_en=subtitle,
            output_path=out_png)
        return out_png if r.get("success") else None
    except Exception:
        return None


def render_charts(stats_bundle: dict, wordcloud_data: dict, *,
                  project: str, version: str, report_name: str,
                  outdir: str) -> dict:
    """真路径：逐张渲染为本地 PNG（落 outdir），封面 best-effort。

    图片由飞书交付层（feishu.create_delivery）用 medias/upload_all 上传本地二进制。
    每张图返回 local_path（绝对路径）；url 置 None。
    """
    if not renderer_available():
        raise RuntimeError("图表渲染器不可用（缺 node_modules 或 node）")

    specs = build_chart_specs(stats_bundle, wordcloud_data)
    os.makedirs(outdir, exist_ok=True)
    charts_out: list[dict] = []
    for spec in specs:
        png = os.path.join(outdir, f"{spec['chart_id']}.png")
        if spec.get("chart_type") == "wordcloud":
            _render_wordcloud(spec, png)
        else:
            _render_png(spec, png)                # 逐张生成 + 自检
        charts_out.append({"chart_id": spec["chart_id"], "title": spec["title"],
                           "chart_type": spec["chart_type"], "local_path": png,
                           "url": None, "source_mode": spec.get("source_mode")})

    cover = {"local_path": None, "url": None, "degraded": True}
    cover_png = _gen_cover(report_name, f"版本 {version}",
                           os.path.join(outdir, "cover.png"))
    if cover_png:
        cover = {"local_path": cover_png, "url": None, "degraded": False}

    return {"charts": charts_out, "cover": cover, "project": project,
            "version": version, "report_name": report_name,
            "chart_count": len(charts_out), "degraded": False}


# ── 降级：同构 chart_urls（结构真实，local_path=None 表无图可嵌）────────
def fallback(stats_bundle: dict, wordcloud_data: dict, *, project: str,
             version: str, report_name: str) -> dict:
    """无渲染器/失败降级：返回同构 chart_urls（degraded=True）。

    每张图带确定性 spec 但 local_path=None / url=None（无渲染器即无图），结构可被
    write_report 与飞书交付无差别消费（缺图跳过，不塞死链）。封面同样降级。
    """
    specs = build_chart_specs(stats_bundle, wordcloud_data)
    charts_out = []
    for spec in specs:
        charts_out.append({
            "chart_id": spec["chart_id"], "title": spec["title"],
            "chart_type": spec["chart_type"],
            "local_path": None, "url": None,
            "source_mode": spec.get("source_mode"),
            "spec": spec,          # 降级保留 spec，便于后续在真环境补渲染
            "degraded": True,
        })
    return {"charts": charts_out,
            "cover": {"local_path": None, "url": None, "degraded": True},
            "project": project, "version": version, "report_name": report_name,
            "chart_count": len(charts_out), "degraded": True}
