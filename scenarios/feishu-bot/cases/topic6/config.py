"""topic6 case 的配置:飞书凭据 + topic6 专属的 coordinator/environment/memory。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Optional


REQUIRED_KEYS = [
    "ARK_API_KEY",
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    "TOPIC6_COORDINATOR_AGENT_ID",
    "TOPIC6_ENVIRONMENT_ID",
]


@dataclass(frozen=True)
class Topic6Config:
    ark_api_key: str
    ark_base_url: str
    feishu_app_id: str
    feishu_app_secret: str
    coordinator_agent_id: str
    environment_id: str
    memory_store_id: str
    pipeline_db_path: str
    vault_id: str
    authorized_open_ids: tuple[str, ...]


def load_case_config(env: Optional[Mapping[str, str]] = None) -> Topic6Config:
    environ = os.environ if env is None else env
    missing = [k for k in REQUIRED_KEYS if not (environ.get(k) or "").strip()]
    if missing:
        raise RuntimeError(f"缺少环境变量:{', '.join(missing)}")
    open_ids = tuple(
        i.strip()
        for i in (environ.get("AUTHORIZED_OPEN_IDS") or "").replace(",", " ").split()
        if i.strip()
    )
    return Topic6Config(
        ark_api_key=environ["ARK_API_KEY"],
        ark_base_url=(environ.get("ARK_BASE_URL") or "https://ark.cn-beijing.volces.com/api/v3").rstrip("/"),
        feishu_app_id=environ["FEISHU_APP_ID"],
        feishu_app_secret=environ["FEISHU_APP_SECRET"],
        coordinator_agent_id=environ["TOPIC6_COORDINATOR_AGENT_ID"].strip(),
        environment_id=environ["TOPIC6_ENVIRONMENT_ID"].strip(),
        memory_store_id=(environ.get("TOPIC6_MEMORY_STORE_ID") or "").strip(),
        pipeline_db_path=(environ.get("TOPIC6_PIPELINE_DB_PATH") or "").strip(),
        vault_id=(environ.get("ARK_VAULT_ID") or "").strip(),
        authorized_open_ids=open_ids,
    )
