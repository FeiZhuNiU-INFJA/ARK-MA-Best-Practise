"""原地更新数字员工的方舟 Agent（改 system prompt / 模型 / bot 名字，Agent ID 不变）。

方舟的 system prompt 是建 Agent 时静态写死的，改了 shared.GROUP_BOT_SYSTEM_TEMPLATE 或
想换 bot 名字（GROUP_BOT_DISPLAY_NAME）后，跑这个脚本把最新配置推上去即可——**不重扫码、
不新建 Agent、GROUP_BOT_AGENT_ID 不变**，运行入口无需改任何环境变量。

方舟更新语义：POST /agents/{id}，body 须带当前 version（不匹配则失败），见 ark.update_agent。

运行：
  set -a && source ~/.arkagent/config.env && set +a   # 需要 ARK_API_KEY[/ARK_BASE_URL] + GROUP_BOT_AGENT_ID
  python scenarios/feishu-bot/cases/digital-employee/update_digital_employee_agent.py

可选环境变量（同 create_digital_employee_agent.py）：
  GROUP_BOT_MODEL_ID      默认 doubao-seed-evolving
  GROUP_BOT_DISPLAY_NAME  默认「数字员工阿J」，写进 system prompt 供模型识别「@谁=在叫自己」，
                          应与飞书开放平台配的机器人显示名一致。
"""
from __future__ import annotations

import asyncio
import os

from shared import GROUP_BOT_NAME, build_group_system

from arkagent.ark import ArkClient
from arkagent.gateway import ConfigStore, DigitalEmployee, MAControlPlane

# 与 create 脚本共用的默认 persona id。
DEFAULT_PERSONA_ID = "emp_group_bot_default"


async def _main() -> None:
    api_key = (os.environ.get("ARK_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("缺少 ARK_API_KEY。请先 export 或 source 主包 config.env。")
    agent_id = (os.environ.get("GROUP_BOT_AGENT_ID") or "").strip()
    if not agent_id:
        raise RuntimeError(
            "缺少 GROUP_BOT_AGENT_ID。这是要更新的现有 Agent id——先跑一次 "
            "create_digital_employee_agent.py 或 initialize_digital_employee.py 建出 Agent，"
            "或手动 export。"
        )
    base_url = (os.environ.get("ARK_BASE_URL") or "https://ark.cn-beijing.volces.com/api/v3").rstrip("/")
    model_id = (os.environ.get("GROUP_BOT_MODEL_ID") or "doubao-seed-evolving").strip()
    # bot 在飞书群里的显示名，写进 system prompt 供模型识别「@谁=在叫自己」；应与开放平台一致。
    bot_name = (os.environ.get("GROUP_BOT_DISPLAY_NAME") or "数字员工阿J").strip()

    ark = ArkClient(api_key, base_url)
    store = ConfigStore()
    plane = MAControlPlane(ark, store)
    try:
        # 走控制面 sync_employee：带 ark_agent_id → get_agent 读回 version → update_agent（Agent ID 不变）。
        persona = store.get_employee(DEFAULT_PERSONA_ID) or DigitalEmployee(
            id=DEFAULT_PERSONA_ID, name=GROUP_BOT_NAME
        )
        old_version = persona.ark_agent_version
        persona.name = GROUP_BOT_NAME
        persona.identity_prompt = build_group_system(bot_name)
        persona.model_id = model_id
        persona.ark_agent_id = agent_id  # 以入口 env 指定的现有 Agent 为准
        persona = await plane.sync_employee(persona)
    finally:
        await ark.aclose()
        store.close()

    print(
        f"已更新飞书数字员工 Agent：{persona.ark_agent_id}"
        f"（版本 {old_version or '?'} → {persona.ark_agent_version}）"
    )
    print(f"  模型：{model_id}")
    print(f"  bot 名字（system prompt）：{bot_name}")
    print("  GROUP_BOT_AGENT_ID 不变，运行入口无需改环境变量，重启即可生效。")


if __name__ == "__main__":
    asyncio.run(_main())
