"""群聊共享 Bot 的一键初始化：扫码建飞书应用 + 建 Bot-only Agent + 落 config.env。

把手动的「阶段 1/2/4」合并成一条命令（阶段 3「去开放平台发布版本」是浏览器操作，
没有 API 可替代，脚本只会在最后提醒你去做）：

  1. 扫码创建一个新的飞书应用（复用主包 node-helper/register_app.mjs）。
  2. 把新 FEISHU_APP_ID/SECRET 就地写回 ~/.arkagent/config.env（保留其余键、权限 0600）。
  3. 用现有 ARK_API_KEY 建一个群聊 Bot-only Agent，并把 GROUP_BOT_AGENT_ID 也写回 config.env。

ARK_API_KEY / ARK_BASE_URL / ARK_ENVIRONMENT_ID 直接沿用 config.env 里已有的，不重建、
不碰 MCP / Vault / static_bearer（那些是四卡点 case 的东西，群聊 Bot 用不到）。

运行（无需先 source，脚本会自己读 config.env）：
  python scenarios/feishu-bot/cases/group-bot/init_group_bot.py

可选：ARK_API_KEY / ARK_BASE_URL / GROUP_BOT_MODEL_ID 用环境变量覆盖 config.env 里的值。
"""
from __future__ import annotations

import asyncio
import os

from shared import build_group_agent_config  # 同目录；导入时会把仓库根加进 sys.path

from arkagent.ark import ArkClient
from arkagent.config import parse_env_text, update_env_file
from arkagent.node_helper import register_feishu_app
from arkagent.paths import get_arkagent_paths

DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
DEFAULT_MODEL_ID = "doubao-seed-2-1-pro-260628"


def _load_existing(config_path: str) -> dict[str, str]:
    if not os.path.exists(config_path):
        return {}
    with open(config_path, "r", encoding="utf-8") as fh:
        return parse_env_text(fh.read())


def _pick(key: str, existing: dict[str, str], default: str = "") -> str:
    """取值优先级：进程环境变量 > config.env 已有值 > 默认。"""
    return (os.environ.get(key) or existing.get(key) or default).strip()


def _ensure_config_file(config_path: str) -> None:
    """update_env_file 需要文件已存在；缺失时先建目录 + 空文件（0700/0600）。"""
    import stat

    if os.path.exists(config_path):
        return
    directory = os.path.dirname(config_path) or "."
    os.makedirs(directory, mode=0o700, exist_ok=True)
    with open(config_path, "w", encoding="utf-8"):
        pass
    os.chmod(config_path, stat.S_IRUSR | stat.S_IWUSR)


async def _create_group_agent(api_key: str, base_url: str, model_id: str) -> str:
    ark = ArkClient(api_key, base_url)
    try:
        agent = await ark.create_agent(build_group_agent_config(model_id))
    finally:
        await ark.aclose()
    return str(agent["id"])


def _main() -> None:
    config_path = get_arkagent_paths().config_path
    existing = _load_existing(config_path)

    api_key = _pick("ARK_API_KEY", existing)
    if not api_key:
        raise RuntimeError(
            f"缺少 ARK_API_KEY（既不在环境变量，也不在 {config_path}）。"
            "请先跑一次主包 arkagent init，或手动 export ARK_API_KEY。"
        )
    base_url = _pick("ARK_BASE_URL", existing, DEFAULT_BASE_URL).rstrip("/")
    model_id = _pick("GROUP_BOT_MODEL_ID", existing, DEFAULT_MODEL_ID)

    # ---- 阶段 1：扫码建飞书应用 ----
    print("【1/3】即将打开扫码建应用流程，请用飞书扫码确认……")
    creds = register_feishu_app()
    print(f"      飞书应用已创建：{creds.app_id}")

    # ---- 阶段 2（上半）：先把飞书凭证落盘，避免后续失败丢掉刚扫的码 ----
    _ensure_config_file(config_path)
    update_env_file(
        config_path,
        {"FEISHU_APP_ID": creds.app_id, "FEISHU_APP_SECRET": creds.app_secret},
    )
    print(f"      已写入 FEISHU_APP_ID/SECRET → {config_path}")

    # ---- 阶段 4：建群聊 Bot-only Agent ----
    print("【2/3】正在创建群聊 Bot-only Agent……")
    agent_id = asyncio.run(_create_group_agent(api_key, base_url, model_id))
    print(f"      已创建群聊共享 Agent：{agent_id}")

    # ---- 阶段 2（下半）：Agent ID 也写回 config.env ----
    update_env_file(config_path, {"GROUP_BOT_AGENT_ID": agent_id})
    print(f"【3/3】已写入 GROUP_BOT_AGENT_ID → {config_path}")

    print()
    print("=" * 60)
    print("初始化完成。还差手动的两步（脚本无法代劳）：")
    print("  · 阶段 3：去飞书开放平台 https://open.feishu.cn/app 打开这个新应用，")
    print("    确认权限（im:message:send_as_bot / im:message / im:message.group_msg / im:chat:readonly）、")
    print("      注意 im:message.group_msg（读取群消息）单列——读整段群历史必需，缺它 list_messages 会 400；")
    print("    事件订阅（长连接 + im.message.receive_v1）、开启机器人能力，然后【发布版本】。")
    print("  · 启动 demo（会自己 source 出这些变量）：")
    print("      set -a && source ~/.arkagent/config.env && set +a")
    print("      python scenarios/feishu-bot/cases/group-bot/client_serial_bot.py")
    print("=" * 60)


if __name__ == "__main__":
    _main()
