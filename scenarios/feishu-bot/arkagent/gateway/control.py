"""ark-gateway 控制面：配置库 → 方舟资源的单向同步与置备（Agent 定义层）。

方案 A 下配置库是全量权威，方舟侧是被同步的下游。本面集中「建/更新 Agent、懒建 Memory Store、
幂等置备 Environment/Vault」，供管理系统与 CLI 脚本共用，便于失败重试与状态回填。

依赖注入：
  - ``ark``：ArkClient（MA REST 裸封装）。
  - ``store``：ConfigStore（配置库）。
  - ``env_provisioner`` / ``vault_provisioner``（可选）：飞书场景的 lark-cli 资源置备器
    （即 case 目录的 shared.ensure_lark_cli_environment / ensure_lark_cli_vault）。以可调用对象
    注入，避免 gateway 包反向依赖 case 目录；未注入时 ensure_environment/ensure_vault 报错提示。
"""
from __future__ import annotations

import time
from typing import Awaitable, Callable, Optional

from ..ark import ArkClient
from .agent_config import build_agent_config
from .config_store import (
    SYNC_FAILED,
    SYNC_SYNCED,
    CapabilityBundle,
    ConfigStore,
    DigitalEmployee,
    Project,
)

EnvProvisioner = Callable[..., Awaitable[str]]
VaultProvisioner = Callable[..., Awaitable[str]]


class MAControlPlane:
    """控制面：把配置库内容单向推送 / 置备到方舟。"""

    def __init__(
        self,
        ark: ArkClient,
        store: ConfigStore,
        *,
        env_provisioner: Optional[EnvProvisioner] = None,
        vault_provisioner: Optional[VaultProvisioner] = None,
    ) -> None:
        self._ark = ark
        self._store = store
        self._env_provisioner = env_provisioner
        self._vault_provisioner = vault_provisioner

    @property
    def ark(self) -> ArkClient:
        """底层 ArkClient（管理系统透传 memory API 时用）。"""
        return self._ark

    @property
    def store(self) -> ConfigStore:
        """底层配置库（便于上层复用同一连接）。"""
        return self._store

    # ---- Agent 定义装配 ----------------------------------------------------
    def build_agent_config(
        self, employee: DigitalEmployee, bundle: Optional[CapabilityBundle] = None
    ) -> dict:
        """参数化装配 Agent 定义。bundle 为 None 时按 employee.bundle_id 自动查配置库。"""
        if bundle is None and employee.bundle_id:
            bundle = self._store.get_bundle(employee.bundle_id)
        return build_agent_config(employee, bundle)

    # ---- 同步：建 / 更新 Agent --------------------------------------------
    async def sync_employee(self, employee: DigitalEmployee) -> DigitalEmployee:
        """无 ark_agent_id 则 create_agent；有则 get_agent→带 version 调 update_agent。

        成功回填 id/version/status=synced；失败回填 status=failed + sync_error 并重新抛出。
        """
        config = self.build_agent_config(employee)
        try:
            if not employee.ark_agent_id:
                created = await self._ark.create_agent(config)
                employee.ark_agent_id = created["id"]
                employee.ark_agent_version = str(created.get("version") or "")
                action = "create"
            else:
                current = await self._ark.get_agent(employee.ark_agent_id)
                if current.get("version") is None:
                    raise RuntimeError(
                        f"无法获取 Agent {employee.ark_agent_id} 的当前版本，无法更新。"
                    )
                updated = await self._ark.update_agent(
                    employee.ark_agent_id, config, int(current["version"])
                )
                employee.ark_agent_version = str(updated.get("version") or "")
                action = "update"
        except Exception as error:  # noqa: BLE001 - 同步失败要落状态供重试
            employee.sync_status = SYNC_FAILED
            employee.sync_error = str(error)[:500]
            self._store.upsert_employee(employee)
            self._store.log_sync(
                "digital_employee", employee.id, "sync", SYNC_FAILED, str(error)[:1000]
            )
            raise

        employee.sync_status = SYNC_SYNCED
        employee.sync_error = ""
        employee.synced_at = int(time.time())
        self._store.upsert_employee(employee)
        self._store.log_sync("digital_employee", employee.id, action, SYNC_SYNCED)
        return employee

    # ---- 同步：懒建项目共享 Memory Store ----------------------------------
    async def sync_project_memory(self, project: Project) -> Project:
        """项目共享群记忆 Store 懒创建：已有 memory_store_id 则跳过，否则 create 并回填。"""
        if project.memory_store_id:
            return project
        store_id = await self._ark.create_memory_store(
            f"feishu-project-{project.id}",
            f"Shared long-term memory for project {project.name} and all its Feishu groups.",
        )
        project.memory_store_id = store_id
        self._store.upsert_project(project)
        self._store.log_sync("project", project.id, "create_memory_store", SYNC_SYNCED)
        return project

    # ---- 幂等置备：Environment / Vault ------------------------------------
    async def ensure_environment(self, feishu_app_id: str, name_hint: str = "group-bot") -> str:
        if self._env_provisioner is None:
            raise RuntimeError(
                "未注入 env_provisioner（如 shared.ensure_lark_cli_environment），无法置备 Environment。"
            )
        return await self._env_provisioner(self._ark, feishu_app_id, name_hint)

    async def ensure_vault(
        self, feishu_app_id: str, tenant_access_token: str, name_hint: str = "group-bot"
    ) -> str:
        if self._vault_provisioner is None:
            raise RuntimeError(
                "未注入 vault_provisioner（如 shared.ensure_lark_cli_vault），无法置备 Vault。"
            )
        return await self._vault_provisioner(
            self._ark, feishu_app_id, tenant_access_token, name_hint
        )
