from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

CASE_DIR = Path(__file__).resolve().parents[1] / "cases" / "topic6"
spec = importlib.util.spec_from_file_location("topic6_case_config", CASE_DIR / "config.py")
config_module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules["topic6_case_config"] = config_module
spec.loader.exec_module(config_module)


def _env(**overrides):
    env = {
        "ARK_API_KEY": "ark-key",
        "FEISHU_APP_ID": "cli-test",
        "FEISHU_APP_SECRET": "secret",
        "TOPIC6_COORDINATOR_AGENT_ID": "agent-test",
        "TOPIC6_ENVIRONMENT_ID": "env-test",
    }
    env.update(overrides)
    return env


def test_artifact_sync_defaults_to_case_data_directory():
    config = config_module.load_case_config(_env())

    assert config.artifact_sync_dir == str(CASE_DIR / "data" / "artifacts")
    assert config.artifact_poll_interval_sec == 60.0


def test_artifact_sync_settings_are_configurable():
    config = config_module.load_case_config(
        _env(
            TOPIC6_ARTIFACT_SYNC_DIR="/tmp/topic6-artifacts",
            TOPIC6_ARTIFACT_POLL_INTERVAL_SEC="2.5",
        )
    )

    assert config.artifact_sync_dir == "/tmp/topic6-artifacts"
    assert config.artifact_poll_interval_sec == 2.5


def test_artifact_poll_interval_must_be_positive():
    with pytest.raises(RuntimeError, match="必须大于 0"):
        config_module.load_case_config(
            _env(TOPIC6_ARTIFACT_POLL_INTERVAL_SEC="0")
        )
