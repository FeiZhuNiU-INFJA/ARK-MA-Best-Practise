from __future__ import annotations

import json
from pathlib import Path


RESOURCE_DIR = (
    Path(__file__).resolve().parents[1] / "cases" / "topic6" / "ma-resources"
)


def test_coordinator_declares_mcp_toolset_inside_tools():
    config = json.loads(
        (RESOURCE_DIR / "agents" / "coordinator.json").read_text(encoding="utf-8")
    )

    assert "mcp_toolset" not in config
    toolsets = [
        tool for tool in config["tools"] if tool["type"] == "mcp_toolset"
    ]
    assert len(toolsets) == 1
    assert toolsets[0]["mcp_server_name"] == "hot-topics"
    assert toolsets[0]["default_config"] == {
        "permission_policy": {"type": "always_allow"}
    }


def test_coordinator_mcp_servers_and_toolsets_are_one_to_one():
    config = json.loads(
        (RESOURCE_DIR / "agents" / "coordinator.json").read_text(encoding="utf-8")
    )

    server_names = {server["name"] for server in config["mcp_servers"]}
    toolset_names = {
        tool["mcp_server_name"]
        for tool in config["tools"]
        if tool["type"] == "mcp_toolset"
    }
    assert server_names == toolset_names
