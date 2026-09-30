#!/usr/bin/env python3
"""为当前扫码用户预授权 Topic6 妙搭发布，并写入用户专属 Ark Vault。"""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

FEISHU_BOT_DIR = Path(__file__).resolve().parents[2]
if str(FEISHU_BOT_DIR) not in sys.path:
    sys.path.insert(0, str(FEISHU_BOT_DIR))

from arkagent.ark import ArkClient  # noqa: E402
from arkagent.config import load_config_file  # noqa: E402
from arkagent.paths import get_case_paths  # noqa: E402
from pipeline_store import PipelineStore  # noqa: E402
from topic6_user_oauth import (  # noqa: E402
    DeviceAuthorization,
    FeishuOAuth,
    Topic6UserAuthorization,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="扫码授权当前飞书用户执行 Topic6 妙搭发布"
    )
    parser.add_argument(
        "--config",
        help="Topic6 config.env 路径；默认 ~/.arkagent/cases/topic6/config.env",
    )
    parser.add_argument(
        "--expected-open-id",
        default="",
        help="可选：校验扫码账号，防止误用其他飞书账号；不传则自动识别",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="不自动打开系统浏览器",
    )
    parser.add_argument(
        "--no-qr",
        action="store_true",
        help="不在终端显示二维码",
    )
    return parser.parse_args()


def _required_env(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise RuntimeError(f"缺少环境变量：{name}")
    return value


def _show_device(device: DeviceAuthorization, *, open_browser: bool, qr: bool) -> None:
    print("\n请用需要发布妙搭报告的飞书账号完成授权：")
    print(device.verification_url)
    if qr and shutil.which("lark-cli"):
        result = subprocess.run(
            ["lark-cli", "auth", "qrcode", device.verification_url, "--ascii"],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            print(result.stdout.rstrip())
    if open_browser:
        webbrowser.open(device.verification_url)
    print("等待扫码确认，授权完成后脚本会自动继续...")


async def _run(args: argparse.Namespace) -> None:
    case_paths = get_case_paths("topic6")
    config_path = Path(args.config or case_paths.config_path).expanduser()
    if not config_path.is_file():
        raise RuntimeError(f"配置文件不存在：{config_path}")
    load_config_file(str(config_path))

    ark = ArkClient(
        _required_env("ARK_API_KEY"),
        (os.environ.get("ARK_BASE_URL") or "https://ark.cn-beijing.volces.com/api/v3").rstrip(
            "/"
        ),
    )
    store = PipelineStore(
        (os.environ.get("TOPIC6_PIPELINE_DB_PATH") or "").strip() or None
    )
    oauth = FeishuOAuth(
        _required_env("FEISHU_APP_ID"),
        _required_env("FEISHU_APP_SECRET"),
    )
    authorization = Topic6UserAuthorization(store, ark, oauth)
    try:
        open_id = await authorization.authorize(
            expected_open_id=args.expected_open_id.strip(),
            on_device_ready=lambda device: _show_device(
                device,
                open_browser=not args.no_browser,
                qr=not args.no_qr,
            ),
        )
    finally:
        store.close()
        await ark.aclose()

    print(f"\n授权完成：open_id={open_id}")
    print("用户 access token 已写入专属 Ark Vault，refresh token 已保存到本地私有数据库。")
    print("现在可启动 Gateway，或直接在飞书中触发下一轮 Topic6 任务。")


def main() -> int:
    try:
        asyncio.run(_run(_parse_args()))
        return 0
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        return 130
    except Exception as error:  # noqa: BLE001
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
