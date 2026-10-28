"""Configuration for the unified Topicx gateway."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

from ..topic5.config import Topic5Config, load_case_config as load_topic5_config
from ..topic6.config import Topic6Config, load_case_config as load_topic6_config


@dataclass(frozen=True)
class TopicxConfig:
    ark_api_key: str
    ark_base_url: str
    feishu_app_id: str
    feishu_app_secret: str
    authorized_open_ids: tuple[str, ...]
    admin_open_ids: tuple[str, ...]
    router_agent_id: str
    router_environment_id: str
    control_db_path: str
    topic5: Topic5Config
    topic6: Topic6Config


def _ids(value: str) -> tuple[str, ...]:
    return tuple(
        item.strip()
        for item in value.replace(",", " ").split()
        if item.strip()
    )


def load_case_config(
    env: Optional[Mapping[str, str]] = None,
) -> TopicxConfig:
    environ = os.environ if env is None else env
    required = (
        "ARK_API_KEY",
        "FEISHU_APP_ID",
        "FEISHU_APP_SECRET",
        "TOPICX_ROUTER_AGENT_ID",
        "TOPICX_ROUTER_ENVIRONMENT_ID",
        "TOPIC5_COORDINATOR_AGENT_ID",
        "TOPIC5_ENVIRONMENT_ID",
        "TOPIC6_COORDINATOR_AGENT_ID",
        "TOPIC6_ENVIRONMENT_ID",
    )
    missing = [key for key in required if not (environ.get(key) or "").strip()]
    if missing:
        raise RuntimeError(f"缺少环境变量:{', '.join(missing)}")

    authorized = _ids(environ.get("AUTHORIZED_OPEN_IDS") or "")
    admins = _ids(environ.get("TOPICX_ADMIN_OPEN_IDS") or "")
    if len(admins) != len(set(admins)):
        raise RuntimeError("TOPICX_ADMIN_OPEN_IDS 不能包含重复的管理员 open_id")
    if authorized and not set(admins).issubset(set(authorized)):
        raise RuntimeError("TOPICX_ADMIN_OPEN_IDS 必须全部包含在 AUTHORIZED_OPEN_IDS 中")

    default_db = Path(__file__).resolve().parent / "data" / "topicx_control.db"
    topic5 = load_topic5_config(environ)
    topic6 = load_topic6_config(environ)
    return TopicxConfig(
        ark_api_key=environ["ARK_API_KEY"].strip(),
        ark_base_url=(
            environ.get("ARK_BASE_URL")
            or "https://ark.cn-beijing.volces.com/api/v3"
        ).rstrip("/"),
        feishu_app_id=environ["FEISHU_APP_ID"].strip(),
        feishu_app_secret=environ["FEISHU_APP_SECRET"].strip(),
        authorized_open_ids=authorized,
        admin_open_ids=admins,
        router_agent_id=environ["TOPICX_ROUTER_AGENT_ID"].strip(),
        router_environment_id=environ["TOPICX_ROUTER_ENVIRONMENT_ID"].strip(),
        control_db_path=(
            environ.get("TOPICX_CONTROL_DB_PATH") or str(default_db)
        ).strip(),
        topic5=topic5,
        topic6=topic6,
    )
