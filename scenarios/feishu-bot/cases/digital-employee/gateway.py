"""digital-employee case 的 Gateway 入口:装配 TopicSessionBot。

TopicSessionBot 已经实现完整的 accept 生命周期(话题隔离/OAuth/记忆/串行执行),
在此只做参数装配并复用 case 目录的独立 db 路径。
"""
from __future__ import annotations

import asyncio
import os


def build_gateway(config, ark, sender, loop: asyncio.AbstractEventLoop):
    from arkagent.paths import get_case_paths
    from digital_employee import TopicSessionBot  # sys.path 已把 case 目录加进去

    case_paths = get_case_paths("digital-employee")
    # TopicSessionBot 直接读 TOPIC_BOT_DB_PATH,这里显式指向 case 私有目录,避免共用状态。
    os.makedirs(case_paths.case_dir, exist_ok=True)
    os.environ.setdefault(
        "TOPIC_BOT_DB_PATH",
        os.path.join(case_paths.case_dir, "topic_bot_sessions.db"),
    )
    execution_mode = os.environ.get("DIGITAL_EMPLOYEE_EXECUTION_MODE", "serial")
    return TopicSessionBot(config, ark, sender, loop, execution_mode=execution_mode)
