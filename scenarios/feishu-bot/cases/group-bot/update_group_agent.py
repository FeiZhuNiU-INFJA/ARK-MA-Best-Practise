"""原地更新群聊共享 Bot 的方舟 Agent（改 system prompt / 模型 / bot 名字，Agent ID 不变）。

方舟的 system prompt 是建 Agent 时静态写死的，改了 shared.GROUP_BOT_SYSTEM_TEMPLATE 或
想换 bot 名字（GROUP_BOT_DISPLAY_NAME）后，跑这个脚本把最新配置推上去即可——**不重扫码、
不新建 Agent、GROUP_BOT_AGENT_ID 不变**，运行入口无需改任何环境变量。

方舟更新语义：POST /agents/{id}，body 须带当前 version（不匹配则失败），见 ark.update_agent。

运行：
  set -a && source ~/.arkagent/config.env && set +a   # 需要 ARK_API_KEY[/ARK_BASE_URL] + GROUP_BOT_AGENT_ID
  python scenarios/feishu-bot/cases/group-bot/update_group_agent.py

可选环境变量（同 create_group_agent.py）：
  GROUP_BOT_MODEL_ID      默认 doubao-seed-2-1-pro-260628
  GROUP_BOT_DISPLAY_NAME  默认「群助手」，写进 system prompt 供模型识别「@谁=在叫自己」，
                          应与飞书开放平台配的机器人显示名一致。
"""
from __future__ import annotations

import asyncio
import os

from shared import build_group_agent_config

from arkagent.ark import ArkClient


async def _main() -> None:
    api_key = (os.environ.get("ARK_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("缺少 ARK_API_KEY。请先 export 或 source 主包 config.env。")
    agent_id = (os.environ.get("GROUP_BOT_AGENT_ID") or "").strip()
    if not agent_id:
        raise RuntimeError(
            "缺少 GROUP_BOT_AGENT_ID。这是要更新的现有 Agent id——先跑一次 create_group_agent.py "
            "或 init_group_bot.py 建出 Agent，或手动 export。"
        )
    base_url = (os.environ.get("ARK_BASE_URL") or "https://ark.cn-beijing.volces.com/api/v3").rstrip("/")
    model_id = (os.environ.get("GROUP_BOT_MODEL_ID") or "doubao-seed-2-1-pro-260628").strip()
    # bot 在飞书群里的显示名，写进 system prompt 供模型识别「@谁=在叫自己」；应与开放平台一致。
    bot_name = (os.environ.get("GROUP_BOT_DISPLAY_NAME") or "群助手").strip()

    ark = ArkClient(api_key, base_url)
    try:
        # 原地更新须带当前 version：先读回来，方舟据此做乐观并发校验。
        current = await ark.get_agent(agent_id)
        if current.get("version") is None:
            raise RuntimeError(f"无法获取 Agent {agent_id} 的当前版本，无法更新。")
        version = int(current["version"])

        new_config = build_group_agent_config(model_id, bot_name)
        updated = await ark.update_agent(agent_id, new_config, version)
    finally:
        await ark.aclose()

    print(f"已更新群聊共享 Agent：{updated['id']}（版本 {version} → {updated.get('version')}）")
    print(f"  bot 名字（system prompt）：{bot_name}")
    print("  GROUP_BOT_AGENT_ID 不变，运行入口无需改环境变量，重启即可生效。")


if __name__ == "__main__":
    asyncio.run(_main())
