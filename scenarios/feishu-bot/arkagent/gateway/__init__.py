"""ark-gateway：面向火山方舟 Managed Agents 的编排网关。

本包把「配置库 → MA 资源」与「活跃会话编排」拆成两个正交子面：
  - 控制面 ``MAControlPlane``（control.py）：从配置库同步/置备 MA 资源（Agent 定义层）。
  - 数据面 ``MADataPlane``（data.py）：把一条飞书消息装配成一次 create_session（Session 运行层）。
二者共享 ``ConfigStore``（config_store.py，配置库=全量权威）与 ``build_agent_config``（agent_config.py）。

历史兼容：原 ``arkagent/gateway.py`` 模块（串行队列 + 单员工编排 ``Gateway``）已迁到
``gateway/orchestrator.py``，其公开符号在此原样再导出，保持 ``from arkagent.gateway import Gateway``
等旧引用不变。
"""
from __future__ import annotations

from .agent_config import build_agent_config
from .config_store import (
    ConfigStore,
    CapabilityBundle,
    DigitalEmployee,
    FeishuGroupBinding,
    Project,
)
from .control import MAControlPlane
from .data import MADataPlane, RuntimeConfig, ScopeRef
from .orchestrator import (
    DEFAULT_PROGRESS_DELAY_MS,
    Gateway,
    KeyedQueue,
    Reply,
    result_to_reply,
    should_handle_message,
    to_conversation_key,
)

__all__ = [
    # 历史编排层（单员工）
    "Gateway",
    "KeyedQueue",
    "Reply",
    "DEFAULT_PROGRESS_DELAY_MS",
    "result_to_reply",
    "should_handle_message",
    "to_conversation_key",
    # 配置库（全量权威）
    "ConfigStore",
    "DigitalEmployee",
    "Project",
    "FeishuGroupBinding",
    "CapabilityBundle",
    # Agent 定义装配
    "build_agent_config",
    # 控制面
    "MAControlPlane",
    # 数据面
    "MADataPlane",
    "RuntimeConfig",
    "ScopeRef",
]
