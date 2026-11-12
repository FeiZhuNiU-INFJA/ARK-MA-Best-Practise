"""Seedance 独立配置；不读取其他 case 的 Agent / Vault / MCP。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from arkagent.config import DEFAULT_ARK_BASE_URL, _split_open_ids
from arkagent.paths import get_case_paths


def required(env: Mapping[str, str], key: str) -> str:
    value = (env.get(key) or "").strip()
    if not value or "${" in value:
        raise RuntimeError(f"缺少环境变量：{key}")
    return value


def positive_int(env: Mapping[str, str], key: str, default: int) -> int:
    try:
        value = int(env.get(key) or default)
    except (TypeError, ValueError) as error:
        raise RuntimeError(f"{key} 必须是正整数") from error
    if value <= 0:
        raise RuntimeError(f"{key} 必须是正整数")
    return value


@dataclass(frozen=True)
class SeedanceConfig:
    ark_api_key: str
    ark_base_url: str
    feishu_app_id: str
    feishu_app_secret: str
    agent_id: str
    environment_id: str
    database_path: str
    session_timeout_ms: int
    artifact_poll_attempts: int
    artifact_poll_interval_ms: int
    max_input_bytes: int
    max_output_bytes: int
    authorized_open_ids: tuple[str, ...]


def load_case_config(env: Mapping[str, str] | None = None) -> SeedanceConfig:
    values = os.environ if env is None else env
    return SeedanceConfig(
        ark_api_key=required(values, "ARK_API_KEY"),
        ark_base_url=(values.get("ARK_BASE_URL") or DEFAULT_ARK_BASE_URL).rstrip("/"),
        feishu_app_id=required(values, "FEISHU_APP_ID"),
        feishu_app_secret=required(values, "FEISHU_APP_SECRET"),
        agent_id=required(values, "SEEDANCE_AGENT_ID"),
        environment_id=required(values, "SEEDANCE_ENVIRONMENT_ID"),
        database_path=values.get("SEEDANCE_DB_PATH") or get_case_paths("seedance", values).database_path,
        session_timeout_ms=positive_int(values, "SEEDANCE_SESSION_TIMEOUT_MS", 1_800_000),
        artifact_poll_attempts=positive_int(values, "SEEDANCE_ARTIFACT_POLL_ATTEMPTS", 10),
        artifact_poll_interval_ms=positive_int(values, "SEEDANCE_ARTIFACT_POLL_INTERVAL_MS", 2_000),
        max_input_bytes=positive_int(values, "SEEDANCE_MAX_INPUT_BYTES", 30 * 1024 * 1024),
        max_output_bytes=positive_int(values, "SEEDANCE_MAX_OUTPUT_BYTES", 30 * 1024 * 1024),
        authorized_open_ids=_split_open_ids(values.get("SEEDANCE_AUTHORIZED_OPEN_IDS") or ""),
    )
