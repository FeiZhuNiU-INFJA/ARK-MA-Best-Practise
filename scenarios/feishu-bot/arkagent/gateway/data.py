"""ark-gateway 数据面：活跃会话编排（Session 运行层）。

runtime 主要依赖这一面：把「一条飞书消息」翻译成「一次 create_session 的运行参数」。
与控制面正交——控制面操作固定/版本化的 Agent 定义层，数据面操作每次会话可变的
environment / resources / vault_ids / 记忆作用域 / 回复策略。

核心方法：
  - ``resolve_runtime_config(tenant_key, chat_id, chat_type)``：按 ``chat_id`` 查路由表
    → 解析出用哪个 Agent、属哪个项目、共享哪个 Store、可写哪些记忆分类、是否用话题。
    找不到绑定时回退 env 默认（向后兼容单员工模式）。``tenant_key`` 仅作归属属性透传，
    **不参与查找**：chat_id 全局唯一且不跨租户复用，runtime 侧 tenant_key 恒为 "default"，
    进键反而与真实值不一致。
  - ``resolve_memory_scope(message_like, runtime_config)``：群作用域 ``scope_id`` 用 **project_id**
    （而非 chat_id），实现「同项目多群共享群记忆」；单聊个人作用域不变。
  - ``writable_categories_for_scope(...)``：某作用域允许写入的记忆分类子集（可读=全部，可写=子集）。
  - ``decide_reply_strategy(runtime_config)``：据 ``reply_uses_topic`` 返回 ``"thread"`` / ``"chat"``。

不依赖 case 目录：只碰 ``ArkClient`` + ``ConfigStore``；作用域用轻量 ``ScopeRef`` 表达，
由 runtime 侧（memory.py）转成其自己的 ``MemoryScope``，避免 gateway 包反向依赖 case 目录。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol

from ..ark import ArkClient
from .config_store import MEMORY_CATEGORIES, ConfigStore


class _MessageLike(Protocol):
    """resolve_memory_scope 只需要这几个字段，任何满足此形状的对象都可传入。"""

    chat_type: str
    tenant_key: str
    chat_id: str

    @property
    def employee_id(self) -> str: ...


@dataclass(frozen=True)
class ScopeRef:
    """记忆作用域，与 case 目录的 MemoryScope 同构。

    ``tenant_key`` 仅作归属属性保留，**不参与作用域判别**：判别键是
    ``(scope_type, scope_id)``（scope_id 全局唯一，不跨租户复用）。
    """

    tenant_key: str
    scope_type: str  # "user" | "group"
    scope_id: str


@dataclass
class RuntimeConfig:
    """一条消息路由后的运行时配置：runtime 据此装配 create_session 与回复策略。"""

    ark_agent_id: str
    ark_environment_id: str
    project_id: str = ""
    digital_employee_id: str = ""
    memory_store_id: str = ""
    writable_categories: tuple[str, ...] = field(default_factory=lambda: tuple(MEMORY_CATEGORIES))
    reply_uses_topic: bool = True
    multimodal_enabled: bool = True
    markdown_enabled: bool = True
    # False = 未命中路由表，走 env 默认（向后兼容单员工模式）。
    bound: bool = False


class MADataPlane:
    """数据面：会话路由 / 作用域解析 / 回复策略。构造注入 ArkClient + ConfigStore + env 默认。"""

    def __init__(
        self,
        ark: ArkClient,
        store: ConfigStore,
        *,
        default_agent_id: str = "",
        default_environment_id: str = "",
        default_reply_uses_topic: bool = True,
    ) -> None:
        self._ark = ark
        self._store = store
        self._default_agent_id = default_agent_id
        self._default_environment_id = default_environment_id
        self._default_reply_uses_topic = default_reply_uses_topic

    # ---- 路由：消息 → 运行时配置 ------------------------------------------
    def resolve_runtime_config(
        self, tenant_key: str, chat_id: str
    ) -> RuntimeConfig:
        """按 ``chat_id`` 命中路由表返回绑定配置；未命中回退 env 默认。

        ``tenant_key`` 仅为兼容旧签名保留，不参与查找。
        """
        binding = self._store.get_binding(chat_id) if chat_id else None
        if binding is None:
            return RuntimeConfig(
                ark_agent_id=self._default_agent_id,
                ark_environment_id=self._default_environment_id,
                writable_categories=tuple(MEMORY_CATEGORIES),
                reply_uses_topic=self._default_reply_uses_topic,
                bound=False,
            )

        employee = self._store.get_employee(binding.digital_employee_id)
        project = self._store.get_project(binding.project_id)
        agent_id = (employee.ark_agent_id if employee else "") or self._default_agent_id
        writable = (
            tuple(project.writable_memory_categories)
            if project and project.writable_memory_categories
            else tuple(MEMORY_CATEGORIES)
        )
        return RuntimeConfig(
            ark_agent_id=agent_id,
            ark_environment_id=self._default_environment_id,
            project_id=binding.project_id,
            digital_employee_id=binding.digital_employee_id,
            memory_store_id=project.memory_store_id if project else "",
            writable_categories=writable,
            reply_uses_topic=project.reply_uses_topic if project else self._default_reply_uses_topic,
            multimodal_enabled=project.multimodal_enabled if project else True,
            markdown_enabled=project.markdown_enabled if project else True,
            bound=True,
        )

    # ---- 作用域：群记忆用 project_id --------------------------------------
    def resolve_memory_scope(
        self, message: _MessageLike, runtime_config: Optional[RuntimeConfig] = None
    ) -> ScopeRef:
        """单聊 → 个人作用域（employee_id）；群聊 → 群作用域，scope_id 优先用 project_id。

        命中绑定（runtime_config.project_id 非空）时同项目多群共享同一群记忆；
        未绑定则退回 chat_id，与历史「一群一记忆」等价。
        """
        if message.chat_type == "p2p":
            if not message.employee_id:
                raise ValueError("单聊消息缺少员工身份，无法确定个人记忆作用域")
            return ScopeRef(message.tenant_key, "user", message.employee_id)
        project_id = runtime_config.project_id if runtime_config else ""
        scope_id = project_id or message.chat_id
        if not scope_id:
            raise ValueError("群消息缺少 chat_id / project_id，无法确定群记忆作用域")
        return ScopeRef(message.tenant_key, "group", scope_id)

    # ---- 可写子集：可读=全部，可写=项目配置的子集 ------------------------
    def writable_categories_for_scope(self, scope: ScopeRef) -> tuple[str, ...]:
        """某作用域允许写入的记忆分类。个人作用域可写全部；群作用域取项目配置子集。

        群作用域的 ``scope_id`` 即 project_id（见 resolve_memory_scope）；未绑定时它是
        chat_id，``get_project`` 查不到 → 回退全部分类，与历史「群内可写全部」等价。
        """
        if scope.scope_type != "group":
            return tuple(MEMORY_CATEGORIES)
        project = self._store.get_project(scope.scope_id)
        if project and project.writable_memory_categories:
            # 仅保留合法分类，忽略脏数据。
            return tuple(
                c for c in project.writable_memory_categories if c in MEMORY_CATEGORIES
            )
        return tuple(MEMORY_CATEGORIES)

    # ---- 回复策略：话题开关 -----------------------------------------------
    @staticmethod
    def decide_reply_strategy(runtime_config: RuntimeConfig) -> str:
        """开话题 → ``"thread"``（reply_in_thread）；关话题 → ``"chat"``（send_to_chat）。"""
        return "thread" if runtime_config.reply_uses_topic else "chat"

    # ---- 会话参数装配：把项目共享 Store 拼成 memory_store 资源 ------------
    @staticmethod
    def memory_store_resource(store_id: str, instructions: str) -> dict:
        return {
            "type": "memory_store",
            "memory_store_id": store_id,
            "instructions": instructions,
        }
