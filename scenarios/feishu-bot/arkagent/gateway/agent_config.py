"""参数化装配方舟 Agent 定义（Agent 定义层，固定、版本化）。

从配置库实体（DigitalEmployee + 可选 CapabilityBundle）装配出 ``create_agent`` / ``update_agent``
所需的 config dict：identity_prompt→``system``、model_id→``model.id``、bundle.skills→``skills[]``、
bundle.mcp_servers→``mcp_servers[]`` + ``mcp_toolset`` 工具、bundle.builtin_tool_toggles→
``agent_toolset_20260701.configs``，并追加 memory 4 个 custom 工具。

本模块不依赖 case 目录，供控制面 / 管理系统 / CLI 脚本共用。原 ``shared.build_group_agent_config``
在 P2 退化为薄封装：用默认 persona（其 identity_prompt = 渲染后的群 Bot system prompt）调用本函数。
"""
from __future__ import annotations

from typing import Optional

from .config_store import MEMORY_CATEGORIES, CapabilityBundle, DigitalEmployee

DEFAULT_AGENT_DESCRIPTION = "飞书数字员工：群聊使用 Bot 身份，单聊按需使用当前用户只读授权"
DEFAULT_MODEL_ID = "doubao-seed-evolving"
# 与旧 build_group_agent_config 输出对齐，保证薄封装的向后兼容。
DEFAULT_METADATA = {"created_via": "group-bot-demo", "scenario": "feishu-digital-employee"}
DEFAULT_BUILTIN_TOOLS = ("web_search", "web_fetch")


def build_memory_custom_tools() -> list[dict]:
    """长期记忆 4 个 Custom Tool。作用域由外部网关强制绑定，工具不接受 store/scope 参数。"""
    category = {
        "type": "string",
        "enum": list(MEMORY_CATEGORIES),
        "description": "记忆分类。",
    }
    key = {
        "type": "string",
        "description": "稳定业务键，例如 reply-style 或 weekly-report-rule；不要使用 store_id。",
    }
    return [
        {
            "type": "custom",
            "name": "memory_list",
            "description": (
                "列出当前会话有权访问的长期记忆。单聊自动访问当前用户个人记忆；"
                "群聊和群话题自动访问当前群记忆。不能指定或切换作用域。"
            ),
            "input_schema": {
                "type": "object",
                "properties": {"category": category},
            },
        },
        {
            "type": "custom",
            "name": "memory_get",
            "description": (
                "读取当前作用域中指定 key 的长期记忆。只在需要精确读取最新内容时调用。"
            ),
            "input_schema": {
                "type": "object",
                "properties": {"category": category, "key": key},
                "required": ["category", "key"],
            },
        },
        {
            "type": "custom",
            "name": "memory_upsert",
            "description": (
                "在当前作用域新增或修正一条长期记忆。只保存未来仍有价值的稳定事实、偏好、"
                "群约定或决策；不要保存临时对话、凭据或敏感信息。"
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "category": category,
                    "key": key,
                    "content": {
                        "type": "string",
                        "description": "完整的最新记忆内容；更新时会替换该 key 的旧内容。",
                    },
                },
                "required": ["category", "key", "content"],
            },
        },
        {
            "type": "custom",
            "name": "memory_forget",
            "description": (
                "删除当前作用域中指定 key 的长期记忆。仅当用户明确要求遗忘或删除时调用，"
                "不得根据推断主动删除。"
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "category": category,
                    "key": key,
                    "reason": {
                        "type": "string",
                        "description": "用户明确要求删除的简短原因。",
                    },
                },
                "required": ["category", "key", "reason"],
            },
        },
    ]


def _builtin_tool_configs(toggles: dict) -> list[dict]:
    """把 bundle 的内置工具开关转成 agent_toolset_20260701.configs（默认全关，与旧配置对齐）。"""
    configs: list[dict] = []
    for name in DEFAULT_BUILTIN_TOOLS:
        configs.append({"name": name, "enabled": bool(toggles.get(name, False))})
    # bundle 里可能声明了额外内置工具开关（除 web_search/web_fetch 之外）。
    for name, enabled in (toggles or {}).items():
        if name not in DEFAULT_BUILTIN_TOOLS:
            configs.append({"name": name, "enabled": bool(enabled)})
    return configs


def build_agent_config(
    employee: DigitalEmployee,
    bundle: Optional[CapabilityBundle] = None,
) -> dict:
    """从数字员工 + 能力包装配方舟 Agent 定义 dict。

    - ``employee.identity_prompt`` → ``system``（persona 的完整系统提示词，由上游渲染好）。
    - ``employee.model_id`` → ``model.id``。
    - ``bundle.skills`` → ``skills[]``；``bundle.mcp_servers`` → ``mcp_servers[]`` + ``mcp_toolset`` 工具。
    - ``bundle.builtin_tool_toggles`` → ``agent_toolset_20260701.configs``。
    - 始终追加 memory 4 个 custom 工具。

    无 bundle 时退化为「仅内置工具（默认全关）+ memory 工具」，与旧 build_group_agent_config 等价。
    """
    toggles = bundle.builtin_tool_toggles if bundle else {}
    tools: list[dict] = [
        {
            "type": "agent_toolset_20260701",
            "default_config": {"enabled": True},
            "configs": _builtin_tool_configs(toggles),
        }
    ]
    tools += build_memory_custom_tools()

    mcp_servers = list(bundle.mcp_servers) if (bundle and bundle.mcp_servers) else []
    if mcp_servers:
        # 声明了 MCP server 才挂 mcp_toolset，避免默认输出偏离旧配置。
        tools.append({"type": "mcp_toolset", "default_config": {"enabled": True}})

    metadata = dict(DEFAULT_METADATA)
    if employee.id:
        metadata["digital_employee_id"] = employee.id

    config: dict = {
        "name": employee.name,
        "description": DEFAULT_AGENT_DESCRIPTION,
        "model": {"id": employee.model_id or DEFAULT_MODEL_ID},
        "system": employee.identity_prompt,
        "tools": tools,
        "skills": list(bundle.skills) if (bundle and bundle.skills) else [],
        "metadata": metadata,
    }
    if mcp_servers:
        config["mcp_servers"] = mcp_servers
    return config
