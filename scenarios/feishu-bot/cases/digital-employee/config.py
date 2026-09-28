"""digital-employee case 的配置:飞书 + 方舟 GROUP_BOT_AGENT_ID。

沿用原 cases/digital-employee/shared.py 里的 load_group_bot_config,只在这里做一层
薄包装,以对齐 case_registry 的 ``load_case_config(env)`` 接口。
"""
from __future__ import annotations

import os
from typing import Mapping, Optional


def load_case_config(env: Optional[Mapping[str, str]] = None):
    """委托给 shared.load_group_bot_config,允许调用方先覆盖 env。"""
    from shared import load_group_bot_config  # 由 case_registry 把 case 目录加进 sys.path

    if env is not None:
        # 简易的临时覆盖:load_group_bot_config 直接读 os.environ,这里做 snapshot 覆盖。
        old = {k: os.environ.get(k) for k in env}
        os.environ.update({k: v for k, v in env.items() if v is not None})
        try:
            return load_group_bot_config()
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    return load_group_bot_config()
