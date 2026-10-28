"""
SSJH-Social-Stat 交互式取数脚本
三步流程：必填收集 → 方案确认 → 执行取数

用法：
  # Step 2：展示方案（默认模式）
  python scripts/run.py \
    --keywords "圣农炸鸡" "圣农鸡块" \
    --filterword "生鲜鸡|生鸡" \
    --platforms "weixin,douyin,weibo" \
    --start-date "2025-07-02" --end-date "2026-07-02"

  # Step 3：确认执行（可调整 3 个参数后加 --mode execute）
  python scripts/run.py \
    --keywords "圣农炸鸡" "圣农鸡块" \
    --filterword "生鲜鸡|生鸡" \
    --platforms "weixin,douyin,weibo" \
    --start-date "2025-07-02" --end-date "2026-07-02" \
    --msg-type 0 --content-mode UGC --filter-ad \
    --mode execute
"""

import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from skill import DatashuoshuoSocialmedia

PLATFORM_DISPLAY = {
    "weibo": "微博", "weixin": "微信", "xiaohongshu": "小红书",
    "douyin": "抖音", "kuaishou": "快手", "shipinhao": "视频号",
    "bilibili": "B站", "zhihu": "知乎", "news": "新闻", "forum": "论坛", "ecom": "电商",
}
MSG_TYPE_DISPLAY = {"0": "主贴+评论 (0)", "1": "仅主贴 (1)", "2": "仅评论 (2)"}
CONTENT_MODE_DISPLAY = {"ALL": "ALL（全部）", "UGC": "UGC（用户原创）", "PGC": "PGC（机构/媒体）"}
KW_NUMS = "①②③④⑤⑥⑦⑧⑨⑩"


def build_plan_text(keywords, filterword, platforms, start_date, end_date,
                    msg_type, content_mode, filter_ad, no_ocr=False):
    d1 = datetime.strptime(start_date, "%Y-%m-%d")
    d2 = datetime.strptime(end_date,   "%Y-%m-%d")
    days = (d2 - d1).days + 1
    plat_names = " / ".join(PLATFORM_DISPLAY.get(p, p) for p in platforms)

    if len(keywords) == 1:
        kw_lines = f"  {keywords[0]}"
    else:
        kw_lines = "\n".join(
            f"  {KW_NUMS[i] if i < len(KW_NUMS) else str(i+1)} {kw}"
            for i, kw in enumerate(keywords)
        )

    filter_ad_label = "是" if filter_ad else "否"
    ocr_label = "关闭" if no_ocr else "开启"

    lines = [
        "",
        "══════════════════════════════════════════════════════════════════",
        "  取数方案",
        "══════════════════════════════════════════════════════════════════",
        "",
        "关键词组：",
        kw_lines,
        f"排  除  词：{filterword if filterword else '（无）'}",
        f"平    台：{plat_names}",
        f"时    间：{start_date} ~ {end_date}（{days}天）",
        "",
        "────────────────────────────────────────────────────────────────",
        "  以下 3 项影响统计数值，请确认或修改：",
        "────────────────────────────────────────────────────────────────",
        "",
        f" ① 帖子类型 msg_type ─── 当前：{MSG_TYPE_DISPLAY.get(str(msg_type), msg_type)}",
        "    可选：0=主贴+评论 │ 1=仅主贴 │ 2=仅评论",
        "    影响：选0声量翻3-10倍且含评论噪声；选1数据干净但量少",
        "    推荐：品牌声量盘点→1 ┃ 舆情全量监测→0 ┃ 用户反馈分析→2",
        "",
        f" ② 内容模式 content_mode ─── 当前：{CONTENT_MODE_DISPLAY.get(content_mode.upper(), content_mode)}",
        "    可选：ALL=全部 │ UGC=用户原创 │ PGC=机构/媒体",
        "    影响：ALL含品牌官方号→情感偏正；UGC更接近真实口碑",
        "    推荐：口碑分析→UGC ┃ 传播分析→PGC ┃ 全景概览→ALL",
        "",
        f" ③ 过滤广告 filter_ad ─── 当前：{filter_ad_label}",
        "    可选：是 │ 否",
        "    影响：不过滤→品牌投放广告计入声量和互动，数据虚高",
        "    推荐：自然口碑分析→开 ┃ 投放效果评估→关",
        "",
        f" ④ 视频文本识别 OCR ─── 当前：{ocr_label}",
        "    说明：关闭后，关键词不在视频封面文字（cover_ocr_content）中匹配",
        "    推荐：纯内容讨论分析→关闭 ┃ 全量投放声量→开启",
        "",
        "══════════════════════════════════════════════════════════════════",
        "确认执行 / 或指定要修改的参数编号（①②③）",
        "",
    ]
    return "\n".join(lines)


def execute(keywords, filterword, platforms, start_date, end_date,
            msg_type, content_mode, filter_ad, output_dir, no_ocr=False):
    skill = DatashuoshuoSocialmedia()
    results = []

    for i, kw in enumerate(keywords, 1):
        print(f"\n[{i}/{len(keywords)}] 查询关键词：{kw} ...")
        try:
            r = skill.run(
                keyword=kw,
                filterword=filterword,
                platforms=platforms,
                start_date=start_date,
                end_date=end_date,
                msg_type=str(msg_type),
                content_mode=content_mode,
                filter_ad=filter_ad,
                keyword_fields="content,title" if no_ocr else None,
                save=True,
                output_dir=output_dir,
            )
            path = r.get("_json_path", "")
            results.append({"keyword": kw, "result": r, "path": path})
            print(f"  ✓ 声量: {(r.get('total_volume') or 0):,}  互动量: {(r.get('total_interaction') or 0):,}")
            print(f"  保存至: {path}")
        except Exception as e:
            print(f"  ✗ 查询失败: {e}")
            results.append({"keyword": kw, "result": None, "path": None, "error": str(e)})

    print("\n" + "═" * 60)
    print("  取数完成 · 汇总")
    print("═" * 60)
    total_vol = 0
    total_int = 0
    saved_paths = []
    for item in results:
        r = item["result"]
        kw = item["keyword"]
        if r:
            v = r.get("total_volume", 0) or 0
            it = r.get("total_interaction", 0) or 0
            total_vol += v
            total_int += it
            print(f"  {kw}：声量 {v:,}  互动量 {it:,}")
            if item["path"]:
                saved_paths.append(item["path"])
        else:
            print(f"  {kw}：⚠ 失败 — {item.get('error', '')}")

    if len(keywords) > 1:
        print(f"\n  合计声量：{total_vol:,}")
        print(f"  合计互动量：{total_int:,}")

    print("\n已保存文件：")
    for p in saved_paths:
        print(f"  {p}")

    if len(results) > 1:
        merged_path = _save_merged(results, output_dir, start_date, end_date)
        if merged_path:
            print(f"\n合并摘要：{merged_path}")

    return results


def _save_merged(results, output_dir, start_date, end_date):
    valid = [item for item in results if item["result"]]
    if not valid:
        return None

    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    s = start_date.replace("-", "")
    e = end_date.replace("-", "")
    path = os.path.join(output_dir, f"merged_{s}_{e}_{ts}.json")

    total_vol = 0
    total_int = 0
    kw_summary = {}
    for item in valid:
        r = item["result"]
        kw = item["keyword"]
        v = r.get("total_volume", 0) or 0
        it = r.get("total_interaction", 0) or 0
        total_vol += v
        total_int += it
        kw_summary[kw] = {
            "total_volume": v,
            "total_interaction": it,
            "total_sentiment": r.get("total_sentiment"),
            "data_type": r.get("data_type"),
            "json_path": item["path"],
        }

    merged = {
        "meta": {
            "keywords": [item["keyword"] for item in valid],
            "start_date": start_date,
            "end_date": end_date,
        },
        "total_volume_all": total_vol,
        "total_interaction_all": total_int,
        "keywords": kw_summary,
    }

    os.makedirs(output_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    return path


def parse_args():
    parser = argparse.ArgumentParser(description="SSJH-Social-Stat 交互式取数")
    parser.add_argument("--keywords", nargs="+", required=True,
                        help="一个或多个关键词（空格分隔），每个独立查询一次")
    parser.add_argument("--filterword", default="",
                        help="排除词，用 | 连接（如 '生鲜鸡|生鸡'）")
    parser.add_argument("--platforms", default="weibo,weixin,xiaohongshu,douyin",
                        help="逗号分隔的平台列表")
    parser.add_argument("--start-date", required=True, dest="start_date",
                        help="开始日期 YYYY-MM-DD")
    parser.add_argument("--end-date",   required=True, dest="end_date",
                        help="结束日期 YYYY-MM-DD")
    parser.add_argument("--msg-type", default="1", dest="msg_type",
                        choices=["0", "1", "2"],
                        help="帖子类型：0=主贴+评论, 1=仅主贴（默认）, 2=仅评论")
    parser.add_argument("--content-mode", default="ALL", dest="content_mode",
                        choices=["ALL", "UGC", "PGC"],
                        help="内容模式：ALL（默认）/ UGC / PGC")
    parser.add_argument("--filter-ad", action="store_true", dest="filter_ad",
                        help="过滤广告（默认不过滤）")
    parser.add_argument("--no-ocr", action="store_true", dest="no_ocr",
                        help="关闭视频文本识别（matchFields 去除 cover_ocr_content）")
    parser.add_argument("--mode", default="plan", choices=["plan", "execute"],
                        help="plan=展示方案（默认）; execute=执行取数")
    parser.add_argument(
        "--output-dir", dest="output_dir",
        default=os.path.join(os.path.dirname(os.path.dirname(__file__)), "Outputs"),
        help="JSON 结果保存目录（默认 skill/Outputs/）",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    platforms = [p.strip() for p in args.platforms.split(",") if p.strip()]

    if args.mode == "plan":
        print(build_plan_text(
            keywords=args.keywords,
            filterword=args.filterword,
            platforms=platforms,
            start_date=args.start_date,
            end_date=args.end_date,
            msg_type=args.msg_type,
            content_mode=args.content_mode,
            filter_ad=args.filter_ad,
            no_ocr=args.no_ocr,
        ))
    elif args.mode == "execute":
        execute(
            keywords=args.keywords,
            filterword=args.filterword,
            platforms=platforms,
            start_date=args.start_date,
            end_date=args.end_date,
            msg_type=args.msg_type,
            content_mode=args.content_mode,
            filter_ad=args.filter_ad,
            no_ocr=args.no_ocr,
            output_dir=args.output_dir,
        )


if __name__ == "__main__":
    main()
