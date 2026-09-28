"""状态目录解析。

按 case 隔离:每个 case 的运行时状态放在 ``~/.arkagent/cases/{case}/``,
含独立的 ``config.env`` 与 ``gateway.db``,由 ``arkagent run --case`` 加载。
主目录 ``~/.arkagent/`` 仅剩少量共享物:App 级互斥锁 ``feishu.{app_id}.lock``、
以及历史遗留的 ``config.env``(旧客户 A demo,已作废;新 case 请勿写入此文件)。
可用 ``ARKAGENT_HOME`` 环境变量整体重定向。

互斥的真正资源是「同一飞书 App 的 WS 长连接只能被一个进程订阅」,所以锁按
``FEISHU_APP_ID`` 分粒度:不同 case 用不同 Bot 时可并行运行。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional


@dataclass(frozen=True)
class ArkagentPaths:
    state_dir: str
    config_path: str
    database_path: str


@dataclass(frozen=True)
class CasePaths:
    case: str
    case_dir: str
    config_path: str
    database_path: str


def get_arkagent_paths(env: Optional[Mapping[str, str]] = None) -> ArkagentPaths:
    """全局路径:state_dir 用于放共享的历史 config/db。"""
    environ = os.environ if env is None else env
    override = environ.get("ARKAGENT_HOME")
    state_dir = Path(override).resolve() if override else (Path.home() / ".arkagent").resolve()
    return ArkagentPaths(
        state_dir=str(state_dir),
        config_path=str(state_dir / "config.env"),
        database_path=str(state_dir / "gateway.db"),
    )


def get_case_paths(case: str, env: Optional[Mapping[str, str]] = None) -> CasePaths:
    """按 case 名解析独立目录 ``~/.arkagent/cases/{case}/``。"""
    if not case or "/" in case or ".." in case:
        raise ValueError(f"非法 case 名:{case!r}")
    base = get_arkagent_paths(env)
    case_dir = Path(base.state_dir) / "cases" / case
    return CasePaths(
        case=case,
        case_dir=str(case_dir),
        config_path=str(case_dir / "config.env"),
        database_path=str(case_dir / "gateway.db"),
    )


def get_feishu_lock_path(app_id: str, env: Optional[Mapping[str, str]] = None) -> str:
    """按飞书 App id 生成锁路径:同一 Bot 只允许一个 gateway 进程订阅 WS。"""
    if not app_id or "/" in app_id or ".." in app_id:
        raise ValueError(f"非法 FEISHU_APP_ID:{app_id!r}")
    base = get_arkagent_paths(env)
    return str(Path(base.state_dir) / f"feishu.{app_id}.lock")

