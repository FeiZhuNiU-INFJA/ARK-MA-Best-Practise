"""创建飞书数字员工 Agent（支持群聊协作与单聊个人服务）。

跑一次，拿到 agent id，填到 GROUP_BOT_AGENT_ID 环境变量，供数字员工使用。
与主包 arkagent init 建的「客户A销售助手」是两个独立 Agent，互不影响。

运行：
  set -a && source ~/.arkagent/config.env && set +a   # 需要 ARK_API_KEY[/ARK_BASE_URL]
  python scenarios/feishu-bot/cases/digital-employee/create_digital_employee_agent.py
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
    base_url = (os.environ.get("ARK_BASE_URL") or "https://ark.cn-beijing.volces.com/api/v3").rstrip("/")
    model_id = (os.environ.get("GROUP_BOT_MODEL_ID") or "doubao-seed-evolving").strip()
    # bot 在飞书群里的显示名，写进 system prompt 供模型识别「@谁=在叫自己」；应与开放平台一致。
    bot_name = (os.environ.get("GROUP_BOT_DISPLAY_NAME") or "数字员工阿J").strip()

    ark = ArkClient(api_key, base_url)
    try:
        agent = await ark.create_agent(build_group_agent_config(model_id, bot_name))
    finally:
        await ark.aclose()

    print(f"已创建群聊共享 Agent：{agent['id']}")
    print("请设置环境变量后再启动 demo：")
    print(f"  export GROUP_BOT_AGENT_ID={agent['id']}")


if __name__ == "__main__":
    asyncio.run(_main())
