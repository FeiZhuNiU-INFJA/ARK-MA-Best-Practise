"""
报告封面生成脚本 v2（GPT-image-2 模型）
适配飞书文档封面 2000×400

⚠️ GPT 限制：
  - Prompt 必须为纯英文（中文会被 Azure 审核拦截）
  - 最大原生比例 3:1（生成后裁切到 5:1）
  - 宽高需被 16 整除

用法：
  python generate_cover_gpt.py \
    --title "无糖饮料社媒舆情分析" \
    --subtitle "2026年5月消费者洞察报告" \
    --title-en "Sugar-Free Beverage Social Media Sentiment Analysis" \
    --subtitle-en "May 2026 Consumer Insights Report" \
    --output "cover.png"
"""

import argparse
import base64
import os
import requests
from PIL import Image
from io import BytesIO


def generate_report_cover_gpt(
    title: str,
    subtitle: str,
    title_en: str,
    subtitle_en: str,
    output_path: str = "cover.png",
    width: int = 2000,
    height: int = 400,
    api_key: str = None
) -> dict:
    """
    使用 GPT-image-2 生成报告封面图片

    Args:
        title: 中文主标题（用于指示图片内渲染的中文文字）
        subtitle: 中文副标题
        title_en: 英文主标题（用于 prompt 描述）
        subtitle_en: 英文副标题（用于 prompt 描述）
        output_path: 输出路径
        width: 目标宽度（默认 2000）
        height: 目标高度（默认 400）
        api_key: API Key（可选）

    Returns:
        dict: {"success": True/False, "path": ..., "size": ..., "error": ...}
    """
    # 凭据只从入参 / 环境变量读（禁止硬编码真实 key；原客户脚本在此硬编码了 relay key，迁入时移除）。
    if not api_key:
        api_key = os.environ.get("REPORT_COVER_API_KEY")
    if not api_key:
        raise ValueError("缺少 REPORT_COVER_API_KEY（入参或环境变量），无法调用封面生成接口")

    BASE_URL = os.environ.get("REPORT_COVER_API_BASE_URL", "https://bmc-llm-relay.bluemediagroup.cn")

    target_w, target_h = width, height

    # 计算生成尺寸：最大 3:1，被 16 整除
    # 目标 5:1 超过 3:1 限制，生成 3:1 后居中裁切
    gen_w = (target_w + 15) // 16 * 16  # 2000 → 2000 (已对齐)
    gen_h = (gen_w // 3 + 15) // 16 * 16  # 2000/3 ≈ 667 → 672

    # 构建纯英文 prompt（严禁包含任何中文字符，否则触发 moderation_blocked）
    prompt = f'''You are a world-class graphic designer known for modern, design-forward aesthetics with bold simplicity and international appeal.

Create a professional ultra-wide banner cover image (3:1 aspect ratio) for a data analysis report.

## Composition and Layout
- Extremely wide horizontal banner format, like a premium website hero banner
- Left one-third to two-fifths: dedicated text/copy space with generous breathing room
- Right two-fifths to two-thirds: main visual illustration area
- Smooth, natural gradient transition between text area and visual area, no hard edges or abrupt divides
- All key content concentrated in the vertical center band (middle 70% of height)

## Left Side Title Typography
- Main title: "{title_en}" displayed as a SINGLE horizontal line in large, bold Simplified Chinese characters. NEVER split the title into multiple lines.
- Subtitle: "{subtitle_en}" displayed in smaller Simplified Chinese characters, positioned directly below the main title, also on a single line
- If the title is long, reduce font size rather than breaking into two lines
- Apply custom typography design to the main title: strong visual hierarchy, contrast between title and subtitle through size, weight, and spacing
- The title should feel designed, not just typed. Consider subtle typographic treatments that add sophistication
- All text MUST be in Simplified Chinese characters (not English, not Pinyin)
- No additional text, attribution, or signature beyond title and subtitle

## Right Side Visual Illustration
- Creative, modern flat-style illustration that immediately communicates the theme of "{title_en}"
- Use a mix of relevant icons, objects, and abstract data visualization elements (charts, graphs, trend lines) to suggest analytical content
- Elements should be distributed horizontally across the space, creating visual rhythm
- Layer elements at different scales to create depth: some larger foreground elements, some smaller background accents
- Include subtle decorative elements like soft geometric shapes, dots, circles as visual accents

## Color and Style
- Clean, minimal background: white or very light neutral
- High-saturation accent palette: pick 2-3 vibrant complementary colors for illustrations and accents
- Modern, international feel with premium quality design
- Subtle gradient or shadow effects on illustrations to add polish without clutter

## Technical Rules
- No watermark, no signature, no attribution text
- No English text visible anywhere in the final image
- All rendered text must be Simplified Chinese characters only'''

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": "gpt-image-2",
        "prompt": prompt,
        "n": 1,
        "size": f"{gen_w}x{gen_h}"
    }

    try:
        response = requests.post(
            f"{BASE_URL}/v1/images/generations",
            headers=headers,
            json=payload,
            timeout=600
        )

        if response.status_code == 429:
            return {"success": False, "error": "限流 (429)，请等待 10s 后重试"}

        if response.status_code != 200:
            err_data = response.json().get("error", {})
            err_msg = err_data.get("message", "API 调用失败")
            err_code = err_data.get("code", "")
            if err_code == "moderation_blocked":
                return {"success": False, "error": "内容审核拦截 (moderation_blocked)：prompt 中可能包含中文字符，请检查"}
            return {"success": False, "error": f"HTTP {response.status_code}: {err_msg}"}

        result = response.json()
        data = result.get("data", [])
        if not data:
            return {"success": False, "error": "未返回图片数据"}

        item = data[0]
        if "b64_json" in item:
            img_bytes = base64.b64decode(item["b64_json"])
        elif "url" in item:
            img_bytes = requests.get(item["url"], timeout=60).content
        else:
            return {"success": False, "error": f"响应中无 b64_json 或 url，keys: {list(item.keys())}"}

        img = Image.open(BytesIO(img_bytes))
        w, h = img.size

        # 居中裁切到目标比例 (5:1)
        target_ratio = target_w / target_h
        current_ratio = w / h

        if abs(current_ratio - target_ratio) > 0.2:
            if current_ratio > target_ratio:
                new_w = int(h * target_ratio)
                left = (w - new_w) // 2
                img = img.crop((left, 0, left + new_w, h))
            else:
                new_h = int(w / target_ratio)
                top = (h - new_h) // 2
                img = img.crop((0, top, w, top + new_h))

        # 缩放到目标尺寸
        img = img.resize((target_w, target_h), Image.LANCZOS)
        img.save(output_path)
        return {"success": True, "path": output_path, "size": (target_w, target_h)}

    except requests.Timeout:
        return {"success": False, "error": "请求超时（600s），GPT 图像生成较慢请重试"}
    except Exception as e:
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="生成报告封面图片（GPT-image-2）")
    parser.add_argument("--title", required=True, help="中文主标题")
    parser.add_argument("--subtitle", required=True, help="中文副标题")
    parser.add_argument("--title-en", required=True, help="英文主标题（prompt 用）")
    parser.add_argument("--subtitle-en", required=True, help="英文副标题（prompt 用）")
    parser.add_argument("--output", default="cover.png", help="输出路径（默认 cover.png）")
    parser.add_argument("--width", type=int, default=2000, help="目标宽度（默认 2000）")
    parser.add_argument("--height", type=int, default=400, help="目标高度（默认 400）")
    args = parser.parse_args()

    result = generate_report_cover_gpt(
        title=args.title,
        subtitle=args.subtitle,
        title_en=args.title_en,
        subtitle_en=args.subtitle_en,
        output_path=args.output,
        width=args.width,
        height=args.height
    )

    if result["success"]:
        print(f"封面生成成功: {result['path']}  尺寸: {result['size']}")
    else:
        print(f"封面生成失败: {result['error']}")
        exit(1)
