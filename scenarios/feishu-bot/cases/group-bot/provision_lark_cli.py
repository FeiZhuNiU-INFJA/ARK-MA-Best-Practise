"""给**现有**飞书应用 + 现有 Agent 补 lark-cli 能力（不扫码、不新建应用、不新建 Agent）。

与 init_group_bot.py 的区别：init 是「从零一条龙」（扫码建应用 + 建 Agent + 置备 lark-cli），
本脚本只做其中的**置备 lark-cli** 这一步——复用 config.env 里已有的 FEISHU_APP_ID/SECRET，
建（或按名字复用）一个装了 lark-cli 的 Environment + 一个存短期 tenant token 的 Vault，并把
GROUP_BOT_ENVIRONMENT_ID / GROUP_BOT_LARK_VAULT_ID 写回 config.env。两者都幂等，重复跑不堆资源。

GROUP_BOT_AGENT_ID 一概不碰（system prompt 的更新走 update_group_agent.py）。

运行（无需先 source，脚本会自己读 config.env；也可用环境变量覆盖）：
  python scenarios/feishu-bot/cases/group-bot/provision_lark_cli.py

前置：config.env 里已有 ARK_API_KEY、FEISHU_APP_ID、FEISHU_APP_SECRET
（即你已经跑过主包 arkagent init 或本 case 的建应用流程）。
"""
from __future__ import annotations

import asyncio
import os

from shared import (
    ensure_lark_cli_environment,
    ensure_lark_cli_vault,
    fetch_feishu_tenant_access_token,
)

from arkagent.ark import ArkClient
from arkagent.config import parse_env_text, update_env_file
from arkagent.paths import get_arkagent_paths

DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"


def _load_existing(config_path: str) -> dict[str, str]:
    if not os.path.exists(config_path):
        return {}
    with open(config_path, "r", encoding="utf-8") as fh:
        return parse_env_text(fh.read())


def _pick(key: str, existing: dict[str, str], default: str = "") -> str:
    """取值优先级：进程环境变量 > config.env 已有值 > 默认。"""
    return (os.environ.get(key) or existing.get(key) or default).strip()


async def _provision(api_key: str, base_url: str, app_id: str, app_secret: str) -> tuple[str, str]:
    ark = ArkClient(api_key, base_url)
    try:
        token = await fetch_feishu_tenant_access_token(app_id, app_secret)
        environment_id = await ensure_lark_cli_environment(ark, app_id)
        vault_id = await ensure_lark_cli_vault(ark, app_id, token.value)
    finally:
        await ark.aclose()
    return environment_id, vault_id


def _main() -> None:
    config_path = get_arkagent_paths().config_path
    existing = _load_existing(config_path)

    api_key = _pick("ARK_API_KEY", existing)
    app_id = _pick("FEISHU_APP_ID", existing)
    app_secret = _pick("FEISHU_APP_SECRET", existing)
    missing = [k for k, v in (("ARK_API_KEY", api_key), ("FEISHU_APP_ID", app_id),
                              ("FEISHU_APP_SECRET", app_secret)) if not v]
    if missing:
        raise RuntimeError(
            f"缺少 {', '.join(missing)}（既不在环境变量，也不在 {config_path}）。"
            "本脚本复用现有飞书应用凭据，请确认已跑过建应用流程。"
        )
    base_url = _pick("ARK_BASE_URL", existing, DEFAULT_BASE_URL).rstrip("/")

    print("正在置备 lark-cli 能力（Environment 装 CLI + Vault 存 tenant token，均按名字幂等）……")
    environment_id, vault_id = asyncio.run(_provision(api_key, base_url, app_id, app_secret))

    update_env_file(
        config_path,
        {"GROUP_BOT_ENVIRONMENT_ID": environment_id, "GROUP_BOT_LARK_VAULT_ID": vault_id},
    )
    print(f"  已写入 GROUP_BOT_ENVIRONMENT_ID={environment_id}")
    print(f"  已写入 GROUP_BOT_LARK_VAULT_ID={vault_id} → {config_path}")
    print()
    print("还差两步（脚本不代劳）：")
    print("  1. 用 update_group_agent.py 把带 lark-cli 段落的新 system prompt 推给现有 Agent。")
    print("  2. 去飞书开放平台给这个应用勾上 lark-cli 要操作的业务域权限（docx / drive / calendar 等），并发布版本。")
    print("  然后 `set -a && source ~/.arkagent/config.env && set +a` 重启 bot；老会话需 /new 重建才会挂上 vault。")


if __name__ == "__main__":
    _main()
