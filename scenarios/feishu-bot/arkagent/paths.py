"""状态目录解析。

按 case 隔离:每个 case 的运行时状态放在 ``~/.arkagent/cases/{dir}/``,
含 ``config.env`` 与 ``gateway.db``,由 ``arkagent run --case`` 加载。
主目录 ``~/.arkagent/`` 仅剩少量共享物:App 级互斥锁 ``feishu.{app_id}.lock``、
以及历史遗留的 ``config.env``(旧客户 A demo,已作废;新 case 请勿写入此文件)。
可用 ``ARKAGENT_HOME`` 环境变量整体重定向；仅需移动互斥锁时可设置
``ARKAGENT_LOCK_DIR``，不会改变配置和数据库路径。

topic 系列(``topic5`` / ``topic6`` / ...)共用同一个飞书 Bot 与方舟 Key,运行时
目录合并到字面名 ``~/.arkagent/cases/topicX/``:共享凭据只存一份,case 专属 ID 以
``TOPIC5_*`` / ``TOPIC6_*`` 前缀在同一 ``config.env`` 区分,天然不冲突。``case``
字段仍保留原始名(topic5/topic6)用于标识,只有落盘目录被归一化。非 topic 系列
(如 ``digital-employee``)仍用各自的字面目录。

互斥的真正资源是「同一飞书 App 的 WS 长连接只能被一个进程订阅」,所以锁按
``FEISHU_APP_ID`` 分粒度:不同 case 用不同 Bot 时可并行运行。topic 系列共用
一个 Bot,本就互斥,共享 ``gateway.db`` 的飞书 event 去重不会冲突。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

# topic5 / topic6 / topic7 ... → 同一个字面目录 topicX。
_TOPIC_SERIES = re.compile(r"^topic\d+$")


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


def _case_dir_name(case: str) -> str:
    """把运行时目录名归一化:topic 系列(topic5/topic6/...)合并到字面 ``topicX``,
    其余 case 用各自的字面名。"""
    return "topicX" if _TOPIC_SERIES.match(case) else case


def get_case_paths(case: str, env: Optional[Mapping[str, str]] = None) -> CasePaths:
    """按 case 名解析运行时目录。

    topic 系列(topic5/topic6/...)落盘到共享目录 ``~/.arkagent/cases/topicX/``,
    其余 case 落盘到 ``~/.arkagent/cases/{case}/``。返回的 ``case`` 字段保留原始名。
    """
    if not case or "/" in case or ".." in case:
        raise ValueError(f"非法 case 名:{case!r}")
    base = get_arkagent_paths(env)
    case_dir = Path(base.state_dir) / "cases" / _case_dir_name(case)
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
    environ = os.environ if env is None else env
    lock_dir = environ.get("ARKAGENT_LOCK_DIR")
    if lock_dir:
        return str(Path(lock_dir).resolve() / f"feishu.{app_id}.lock")
    base = get_arkagent_paths(env)
    return str(Path(base.state_dir) / f"feishu.{app_id}.lock")
