"""数字员工管理系统配置库（全量权威事实来源）。

方案 A：identity 文本、skills/mcp 清单、路由映射、开关全部存这里，再由控制面单向同步到方舟。
与现有会话库 / 记忆库物理分离，独立 SQLite：``data/digital_employee_admin.db``
（可用环境变量 ``DIGITAL_EMPLOYEE_ADMIN_DB_PATH`` 覆盖）。

连接风格对齐 memory.py 的 SqliteMemoryState：``check_same_thread=False`` + WAL + ``chmod 0o600``，
JSON 字段用 TEXT 存序列化。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# 与 memory.py 的 MEMORY_CATEGORIES 保持一致；此处复制常量避免 gateway 包反向依赖 case 目录。
MEMORY_CATEGORIES = (
    "profile",
    "preferences",
    "facts",
    "decisions",
    "conventions",
    "notes",
)

DEFAULT_ADMIN_DB_PATH = str(
    Path(__file__).resolve().parents[3] / "data" / "digital_employee_admin.db"
)

# 同步状态取值。
SYNC_PENDING = "pending"
SYNC_SYNCED = "synced"
SYNC_FAILED = "failed"


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _now() -> int:
    return int(time.time())


def _dumps(value: object) -> str:
    return json.dumps(value or [], ensure_ascii=False)


def _loads(value: Optional[str], default: object) -> object:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


# ---- 实体模型 --------------------------------------------------------------


@dataclass
class DigitalEmployee:
    """数字员工 / persona ⇔ 方舟侧一个 Agent 定义。"""

    id: str
    name: str
    identity_prompt: str = ""
    model_id: str = "doubao-seed-evolving"
    bundle_id: Optional[str] = None
    ark_agent_id: str = ""
    ark_agent_version: str = ""
    sync_status: str = SYNC_PENDING
    synced_at: Optional[int] = None
    sync_error: str = ""
    created_at: int = field(default_factory=_now)
    updated_at: int = field(default_factory=_now)


@dataclass
class Project:
    """项目：一个项目含多个飞书群，群间共享群记忆（scope 键 = project_id）。"""

    id: str
    name: str
    description: str = ""
    memory_store_id: str = ""
    writable_memory_categories: list[str] = field(
        default_factory=lambda: list(MEMORY_CATEGORIES)
    )
    reply_uses_topic: bool = True
    multimodal_enabled: bool = True
    markdown_enabled: bool = True
    created_at: int = field(default_factory=_now)
    updated_at: int = field(default_factory=_now)


@dataclass
class FeishuGroupBinding:
    """路由表：``chat_id`` → project_id + digital_employee_id。

    ``tenant_key`` 仅作归属/治理属性保留，**不参与主键**：chat_id 全局唯一且不跨租户复用。
    """

    chat_id: str
    project_id: str
    digital_employee_id: str
    tenant_key: str = "default"
    created_at: int = field(default_factory=_now)
    updated_at: int = field(default_factory=_now)


@dataclass
class CapabilityBundle:
    """能力包 / access bundle：具名可复用的能力 / 权限集合。"""

    id: str
    name: str
    skills: list[dict] = field(default_factory=list)
    mcp_servers: list[dict] = field(default_factory=list)
    builtin_tool_toggles: dict = field(
        default_factory=lambda: {"web_search": False, "web_fetch": False}
    )
    writable_memory_categories: list[str] = field(
        default_factory=lambda: list(MEMORY_CATEGORIES)
    )
    created_at: int = field(default_factory=_now)
    updated_at: int = field(default_factory=_now)


# ---- DAO -------------------------------------------------------------------


class ConfigStore:
    """配置库 = 全量权威。控制面与数据面共同依赖。"""

    def __init__(self, db_path: Optional[str] = None):
        path = db_path or os.environ.get("DIGITAL_EMPLOYEE_ADMIN_DB_PATH") or DEFAULT_ADMIN_DB_PATH
        self._path = Path(path).expanduser()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            str(self._path), check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._create_schema()
        self._protect_files()

    def _create_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS capability_bundle (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                skills TEXT NOT NULL DEFAULT '[]',
                mcp_servers TEXT NOT NULL DEFAULT '[]',
                builtin_tool_toggles TEXT NOT NULL DEFAULT '{}',
                writable_memory_categories TEXT NOT NULL DEFAULT '[]',
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS digital_employee (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                identity_prompt TEXT NOT NULL DEFAULT '',
                model_id TEXT NOT NULL DEFAULT 'doubao-seed-evolving',
                bundle_id TEXT,
                ark_agent_id TEXT NOT NULL DEFAULT '',
                ark_agent_version TEXT NOT NULL DEFAULT '',
                sync_status TEXT NOT NULL DEFAULT 'pending',
                synced_at INTEGER,
                sync_error TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                FOREIGN KEY (bundle_id) REFERENCES capability_bundle(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS project (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                memory_store_id TEXT NOT NULL DEFAULT '',
                writable_memory_categories TEXT NOT NULL DEFAULT '[]',
                reply_uses_topic INTEGER NOT NULL DEFAULT 1,
                multimodal_enabled INTEGER NOT NULL DEFAULT 1,
                markdown_enabled INTEGER NOT NULL DEFAULT 1,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS feishu_group_binding (
                tenant_key TEXT NOT NULL DEFAULT 'default',
                chat_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                digital_employee_id TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (chat_id),
                FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE CASCADE,
                FOREIGN KEY (digital_employee_id) REFERENCES digital_employee(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS sync_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                action TEXT NOT NULL,
                status TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL
            );
            """
        )

    def _protect_files(self) -> None:
        for path in (
            self._path,
            Path(f"{self._path}-wal"),
            Path(f"{self._path}-shm"),
        ):
            if path.exists():
                path.chmod(0o600)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- capability_bundle -------------------------------------------------
    def upsert_bundle(self, bundle: CapabilityBundle) -> CapabilityBundle:
        if not bundle.id:
            bundle.id = _new_id("bundle")
        bundle.updated_at = _now()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO capability_bundle
                    (id, name, skills, mcp_servers, builtin_tool_toggles,
                     writable_memory_categories, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    skills=excluded.skills,
                    mcp_servers=excluded.mcp_servers,
                    builtin_tool_toggles=excluded.builtin_tool_toggles,
                    writable_memory_categories=excluded.writable_memory_categories,
                    updated_at=excluded.updated_at
                """,
                (
                    bundle.id,
                    bundle.name,
                    _dumps(bundle.skills),
                    _dumps(bundle.mcp_servers),
                    json.dumps(bundle.builtin_tool_toggles, ensure_ascii=False),
                    _dumps(bundle.writable_memory_categories),
                    bundle.created_at,
                    bundle.updated_at,
                ),
            )
        return bundle

    def get_bundle(self, bundle_id: str) -> Optional[CapabilityBundle]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM capability_bundle WHERE id = ?", (bundle_id,)
            ).fetchone()
        return self._row_to_bundle(row) if row else None

    def list_bundles(self) -> list[CapabilityBundle]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM capability_bundle ORDER BY updated_at DESC"
            ).fetchall()
        return [self._row_to_bundle(row) for row in rows]

    def delete_bundle(self, bundle_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM capability_bundle WHERE id = ?", (bundle_id,)
            )

    @staticmethod
    def _row_to_bundle(row: sqlite3.Row) -> CapabilityBundle:
        return CapabilityBundle(
            id=row["id"],
            name=row["name"],
            skills=_loads(row["skills"], []),
            mcp_servers=_loads(row["mcp_servers"], []),
            builtin_tool_toggles=_loads(row["builtin_tool_toggles"], {}),
            writable_memory_categories=_loads(row["writable_memory_categories"], []),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # ---- digital_employee --------------------------------------------------
    def upsert_employee(self, employee: DigitalEmployee) -> DigitalEmployee:
        if not employee.id:
            employee.id = _new_id("emp")
        employee.updated_at = _now()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO digital_employee
                    (id, name, identity_prompt, model_id, bundle_id, ark_agent_id,
                     ark_agent_version, sync_status, synced_at, sync_error,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    identity_prompt=excluded.identity_prompt,
                    model_id=excluded.model_id,
                    bundle_id=excluded.bundle_id,
                    ark_agent_id=excluded.ark_agent_id,
                    ark_agent_version=excluded.ark_agent_version,
                    sync_status=excluded.sync_status,
                    synced_at=excluded.synced_at,
                    sync_error=excluded.sync_error,
                    updated_at=excluded.updated_at
                """,
                (
                    employee.id,
                    employee.name,
                    employee.identity_prompt,
                    employee.model_id,
                    employee.bundle_id,
                    employee.ark_agent_id,
                    employee.ark_agent_version,
                    employee.sync_status,
                    employee.synced_at,
                    employee.sync_error,
                    employee.created_at,
                    employee.updated_at,
                ),
            )
        return employee

    def get_employee(self, employee_id: str) -> Optional[DigitalEmployee]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM digital_employee WHERE id = ?", (employee_id,)
            ).fetchone()
        return self._row_to_employee(row) if row else None

    def list_employees(self) -> list[DigitalEmployee]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM digital_employee ORDER BY updated_at DESC"
            ).fetchall()
        return [self._row_to_employee(row) for row in rows]

    def delete_employee(self, employee_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM digital_employee WHERE id = ?", (employee_id,)
            )

    @staticmethod
    def _row_to_employee(row: sqlite3.Row) -> DigitalEmployee:
        return DigitalEmployee(
            id=row["id"],
            name=row["name"],
            identity_prompt=row["identity_prompt"],
            model_id=row["model_id"],
            bundle_id=row["bundle_id"],
            ark_agent_id=row["ark_agent_id"],
            ark_agent_version=row["ark_agent_version"],
            sync_status=row["sync_status"],
            synced_at=row["synced_at"],
            sync_error=row["sync_error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # ---- project -----------------------------------------------------------
    def upsert_project(self, project: Project) -> Project:
        if not project.id:
            project.id = _new_id("proj")
        project.updated_at = _now()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO project
                    (id, name, description, memory_store_id, writable_memory_categories,
                     reply_uses_topic, multimodal_enabled, markdown_enabled,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    description=excluded.description,
                    memory_store_id=excluded.memory_store_id,
                    writable_memory_categories=excluded.writable_memory_categories,
                    reply_uses_topic=excluded.reply_uses_topic,
                    multimodal_enabled=excluded.multimodal_enabled,
                    markdown_enabled=excluded.markdown_enabled,
                    updated_at=excluded.updated_at
                """,
                (
                    project.id,
                    project.name,
                    project.description,
                    project.memory_store_id,
                    _dumps(project.writable_memory_categories),
                    int(project.reply_uses_topic),
                    int(project.multimodal_enabled),
                    int(project.markdown_enabled),
                    project.created_at,
                    project.updated_at,
                ),
            )
        return project

    def get_project(self, project_id: str) -> Optional[Project]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM project WHERE id = ?", (project_id,)
            ).fetchone()
        return self._row_to_project(row) if row else None

    def list_projects(self) -> list[Project]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM project ORDER BY updated_at DESC"
            ).fetchall()
        return [self._row_to_project(row) for row in rows]

    def delete_project(self, project_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM project WHERE id = ?", (project_id,))

    @staticmethod
    def _row_to_project(row: sqlite3.Row) -> Project:
        return Project(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            memory_store_id=row["memory_store_id"],
            writable_memory_categories=_loads(row["writable_memory_categories"], []),
            reply_uses_topic=bool(row["reply_uses_topic"]),
            multimodal_enabled=bool(row["multimodal_enabled"]),
            markdown_enabled=bool(row["markdown_enabled"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # ---- feishu_group_binding（路由表）------------------------------------
    def upsert_binding(self, binding: FeishuGroupBinding) -> FeishuGroupBinding:
        binding.updated_at = _now()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO feishu_group_binding
                    (tenant_key, chat_id, project_id, digital_employee_id,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    tenant_key=excluded.tenant_key,
                    project_id=excluded.project_id,
                    digital_employee_id=excluded.digital_employee_id,
                    updated_at=excluded.updated_at
                """,
                (
                    binding.tenant_key,
                    binding.chat_id,
                    binding.project_id,
                    binding.digital_employee_id,
                    binding.created_at,
                    binding.updated_at,
                ),
            )
        return binding

    def get_binding(self, chat_id: str) -> Optional[FeishuGroupBinding]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM feishu_group_binding WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()
        return self._row_to_binding(row) if row else None

    def list_bindings(
        self, project_id: Optional[str] = None
    ) -> list[FeishuGroupBinding]:
        with self._lock:
            if project_id:
                rows = self._conn.execute(
                    "SELECT * FROM feishu_group_binding WHERE project_id = ? ORDER BY updated_at DESC",
                    (project_id,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM feishu_group_binding ORDER BY updated_at DESC"
                ).fetchall()
        return [self._row_to_binding(row) for row in rows]

    def delete_binding(self, chat_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM feishu_group_binding WHERE chat_id = ?",
                (chat_id,),
            )

    @staticmethod
    def _row_to_binding(row: sqlite3.Row) -> FeishuGroupBinding:
        return FeishuGroupBinding(
            tenant_key=row["tenant_key"],
            chat_id=row["chat_id"],
            project_id=row["project_id"],
            digital_employee_id=row["digital_employee_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # ---- sync_log ----------------------------------------------------------
    def log_sync(
        self, entity_type: str, entity_id: str, action: str, status: str, detail: str = ""
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO sync_log
                    (entity_type, entity_id, action, status, detail, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (entity_type, entity_id, action, status, detail[:1000], _now()),
            )

    def list_sync_logs(self, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM sync_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]
