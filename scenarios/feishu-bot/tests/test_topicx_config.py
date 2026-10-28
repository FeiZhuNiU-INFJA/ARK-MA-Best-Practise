from __future__ import annotations

import pytest

from cases.topicx.config import load_case_config


def _env():
    return {
        "ARK_API_KEY": "ark-placeholder",
        "FEISHU_APP_ID": "app-placeholder",
        "FEISHU_APP_SECRET": "secret-placeholder",
        "AUTHORIZED_OPEN_IDS": "ou-user ou-admin-1 ou-admin-2",
        "TOPICX_ADMIN_OPEN_IDS": "ou-admin-1,ou-admin-2",
        "TOPICX_ROUTER_AGENT_ID": "router-agent",
        "TOPICX_ROUTER_ENVIRONMENT_ID": "router-env",
        "TOPIC5_COORDINATOR_AGENT_ID": "topic5-agent",
        "TOPIC5_ENVIRONMENT_ID": "topic5-env",
        "TOPIC6_COORDINATOR_AGENT_ID": "topic6-agent",
        "TOPIC6_ENVIRONMENT_ID": "topic6-env",
    }


def test_load_topicx_config_aggregates_business_configs(tmp_path):
    env = _env()
    env["TOPICX_CONTROL_DB_PATH"] = str(tmp_path / "control.db")
    config = load_case_config(env)
    assert config.router_agent_id == "router-agent"
    assert config.topic5.coordinator_agent_id == "topic5-agent"
    assert config.topic6.coordinator_agent_id == "topic6-agent"
    assert config.admin_open_ids == ("ou-admin-1", "ou-admin-2")


def test_topicx_allows_everyone_when_authorized_list_is_unset():
    env = _env()
    env.pop("AUTHORIZED_OPEN_IDS")
    config = load_case_config(env)
    assert config.authorized_open_ids == ()
    assert config.admin_open_ids == ("ou-admin-1", "ou-admin-2")


@pytest.mark.parametrize("admins", ["", "ou-admin-1", "ou-admin-1 ou-admin-2"])
def test_topicx_admins_are_optional_and_support_any_count(admins):
    env = _env()
    env.pop("AUTHORIZED_OPEN_IDS")
    env["TOPICX_ADMIN_OPEN_IDS"] = admins
    assert load_case_config(env).admin_open_ids == tuple(admins.split())


def test_topicx_rejects_duplicate_admins():
    env = _env()
    env["TOPICX_ADMIN_OPEN_IDS"] = "ou-admin-1 ou-admin-1"
    with pytest.raises(RuntimeError, match="TOPICX_ADMIN_OPEN_IDS"):
        load_case_config(env)


def test_topicx_admins_must_be_in_nonempty_authorized_list():
    env = _env()
    env["TOPICX_ADMIN_OPEN_IDS"] = "ou-admin-1 ou-missing"
    with pytest.raises(RuntimeError, match="AUTHORIZED_OPEN_IDS"):
        load_case_config(env)
