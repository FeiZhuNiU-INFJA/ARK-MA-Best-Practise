"""
send_plan_card.py — 数说取数方案飞书卡片发送脚本

用法：
  python scripts/send_plan_card.py \\
    --keywords "圣农炸鸡" "圣农鸡块" \\
    --filterword "生鲜鸡|生鸡" \\
    --platforms "weixin,douyin,weibo" \\
    --start-date "2025-07-01" --end-date "2026-07-01" \\
    [--msg-type 1] [--content-mode ALL] [--filter-ad] [--no-ocr]

通过 lark-cli 以 bot 身份向当前会话群发送飞书原生卡片，
展示取数方案的 4 项高影响参数，等待用户回复「确认」或指定修改。
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime

PLATFORM_DISPLAY = {
    "weibo": "微博", "weixin": "微信", "xiaohongshu": "小红书",
    "douyin": "抖音", "kuaishou": "快手", "shipinhao": "视频号",
    "bilibili": "B站", "zhihu": "知乎", "news": "新闻",
    "forum": "论坛", "ecom": "电商",
}
MSG_TYPE_DISPLAY = {
    "0": "主贴 + 评论",
    "1": "仅主贴",
    "2": "仅评论",
}
CONTENT_MODE_DISPLAY = {
    "ALL": "ALL（全部）",
    "UGC": "UGC（用户原创）",
    "PGC": "PGC（机构/媒体）",
}
KW_NUMS = "①②③④⑤⑥⑦⑧⑨⑩"


def build_card(keywords, filterword, platforms, start_date, end_date,
               msg_type, content_mode, filter_ad, no_ocr):
    d1 = datetime.strptime(start_date, "%Y-%m-%d")
    d2 = datetime.strptime(end_date, "%Y-%m-%d")
    days = (d2 - d1).days + 1
    plat_names = " / ".join(PLATFORM_DISPLAY.get(p, p) for p in platforms)

    if len(keywords) == 1:
        kw_text = keywords[0]
    else:
        kw_text = "  ".join(
            f"{KW_NUMS[i] if i < len(KW_NUMS) else str(i+1)} {kw}"
            for i, kw in enumerate(keywords)
        )

    filter_ad_text = "开（过滤广告）" if filter_ad else "关（保留广告）"
    ocr_text       = "关闭" if no_ocr else "开启"

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "📊 数说取数方案 · 请确认"},
            "template": "blue"
        },
        "elements": [
            # 基本参数
            {
                "tag": "div",
                "fields": [
                    {
                        "is_short": True,
                        "text": {"tag": "lark_md", "content": f"**关键词**\n{kw_text}"}
                    },
                    {
                        "is_short": True,
                        "text": {"tag": "lark_md", "content": f"**排除词**\n{filterword if filterword else '（无）'}"}
                    },
                    {
                        "is_short": True,
                        "text": {"tag": "lark_md", "content": f"**平台**\n{plat_names}"}
                    },
                    {
                        "is_short": True,
                        "text": {"tag": "lark_md",
                                 "content": f"**时间范围**\n{start_date} ~ {end_date}（{days}天）"}
                    },
                ]
            },
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": "**以下 4 项参数影响统计数值，请确认或告知修改：**"}
            },
            # 4 项参数
            {
                "tag": "div",
                "fields": [
                    {
                        "is_short": True,
                        "text": {
                            "tag": "lark_md",
                            "content": (
                                f"**① 帖子类型 msg_type**\n"
                                f"当前：{MSG_TYPE_DISPLAY.get(str(msg_type), msg_type)}\n"
                                f"可选：0=主贴+评论  /  1=仅主贴  /  2=仅评论"
                            )
                        }
                    },
                    {
                        "is_short": True,
                        "text": {
                            "tag": "lark_md",
                            "content": (
                                f"**② 内容模式 content_mode**\n"
                                f"当前：{CONTENT_MODE_DISPLAY.get(content_mode.upper(), content_mode)}\n"
                                f"可选：ALL  /  UGC  /  PGC"
                            )
                        }
                    },
                    {
                        "is_short": True,
                        "text": {
                            "tag": "lark_md",
                            "content": (
                                f"**③ 过滤广告 filter_ad**\n"
                                f"当前：{filter_ad_text}\n"
                            )
                        }
                    },
                    {
                        "is_short": True,
                        "text": {
                            "tag": "lark_md",
                            "content": (
                                f"**④ 视频文本识别 OCR**\n"
                                f"当前：{ocr_text}\n"
                            )
                        }
                    },
                ]
            },
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": "回复 **确认** 开始取数，或指定要修改的参数编号（①②③④）及新值。"
                }
            }
        ]
    }
    return card


def send_card(card_json: dict, chat_id: str) -> bool:
    cmd = [
        "lark-cli", "im", "+messages-send",
        "--as", "bot",
        "--chat-id", chat_id,
        "--content", json.dumps(card_json, ensure_ascii=False),
        "--msg-type", "interactive",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[send_plan_card] lark-cli 返回码 {result.returncode}", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        return False
    return True


def parse_args():
    parser = argparse.ArgumentParser(description="发送数说取数方案飞书卡片")
    parser.add_argument("--keywords", nargs="+", required=True)
    parser.add_argument("--filterword", default="")
    parser.add_argument("--platforms", default="weibo,weixin,xiaohongshu,douyin")
    parser.add_argument("--start-date", required=True, dest="start_date")
    parser.add_argument("--end-date", required=True, dest="end_date")
    parser.add_argument("--msg-type", default="1", dest="msg_type",
                        choices=["0", "1", "2"])
    parser.add_argument("--content-mode", default="ALL", dest="content_mode",
                        choices=["ALL", "UGC", "PGC"])
    parser.add_argument("--filter-ad", action="store_true", dest="filter_ad")
    parser.add_argument("--no-ocr", action="store_true", dest="no_ocr")
    parser.add_argument("--chat-id", dest="chat_id",
                        default=None,
                        help="飞书群 chat_id（默认从 CC_SESSION_KEY 解析）")
    parser.add_argument("--dry-run", action="store_true",
                        help="仅打印卡片 JSON，不发送")
    return parser.parse_args()


def main():
    args = parse_args()
    platforms = [p.strip() for p in args.platforms.split(",") if p.strip()]

    card = build_card(
        keywords=args.keywords,
        filterword=args.filterword,
        platforms=platforms,
        start_date=args.start_date,
        end_date=args.end_date,
        msg_type=args.msg_type,
        content_mode=args.content_mode,
        filter_ad=args.filter_ad,
        no_ocr=args.no_ocr,
    )

    if args.dry_run:
        print(json.dumps(card, ensure_ascii=False, indent=2))
        return

    # 解析 chat_id
    chat_id = args.chat_id
    if not chat_id:
        session_key = os.environ.get("CC_SESSION_KEY", "")
        parts = session_key.split(":")
        if len(parts) >= 2:
            chat_id = parts[1]
        else:
            print("[send_plan_card] 错误：无法从 CC_SESSION_KEY 解析 chat_id，"
                  "请通过 --chat-id 传入", file=sys.stderr)
            sys.exit(1)

    ok = send_card(card, chat_id)
    if ok:
        print(f"[send_plan_card] ✅ 卡片已发送至 {chat_id}")
    else:
        print("[send_plan_card] ❌ 卡片发送失败", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
