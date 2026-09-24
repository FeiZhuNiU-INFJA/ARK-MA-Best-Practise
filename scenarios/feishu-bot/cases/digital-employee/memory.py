"""数字员工长期记忆：个人/群作用域、Memory Store 挂载与 Custom Tool CRUD。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sqlite3
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from arkagent.ark import ArkClient
from arkagent.feishu import IncomingMessage

MEMORY_CATEGORIES = (
    "profile",
    "preferences",
    "facts",
    "decisions",
    "conventions",
    "notes",
)
MAX_MEMORY_BYTES = 80 * 1024
MAX_LIST_ITEMS = 100
MAX_ALWAYS_APPLY_CONVENTIONS = 20
MAX_ALWAYS_APPLY_BYTES = 16 * 1024
DEFAULT_MEMORY_DB_PATH = str(
    Path(__file__).resolve().parents[4] / "data" / "digital_employee_memory.db"
)

USER_MEMORY_INSTRUCTIONS = (
    "这是当前单聊用户的个人长期记忆，只能用于当前用户的单聊。按需读取相关文件；"
    "不得向群聊或其他用户泄露其中内容。"
)
GROUP_MEMORY_INSTRUCTIONS = (
    "这是当前群的共享长期记忆，群主时间线和该群所有话题共同使用。"
    "只保存群级事实、约定和决策，不得写入成员私人信息。"
)


@dataclass(frozen=True)
class MemoryScope:
    tenant_key: str
    scope_type: str
    scope_id: str

    @classmethod
    def from_message(cls, message: IncomingMessage) -> "MemoryScope":
        if message.chat_type == "p2p":
            if not message.employee_id:
                raise ValueError("单聊消息缺少员工身份，无法确定个人记忆作用域")
            return cls(message.tenant_key, "user", message.employee_id)
        if not message.chat_id:
            raise ValueError("群消息缺少 chat_id，无法确定群记忆作用域")
        return cls(message.tenant_key, "group", message.chat_id)

    @classmethod
    def legacy_open_id_scope(cls, message: IncomingMessage) -> Optional["MemoryScope"]:
        """返回旧版 open_id 个人记忆作用域，供首次拿到 user_id 时就地迁移。"""
        if (
            message.chat_type == "p2p"
            and message.user_id
            and message.user_open_id
            and message.user_id != message.user_open_id
        ):
            return cls(message.tenant_key, "user", message.user_open_id)
        return None

    def lock_key(self) -> str:
        return f"{self.scope_type}:{self.scope_id}"


class SqliteMemoryState:
    """跨执行模式共享的 Store 映射与 Session 记忆授权绑定。"""

    def __init__(self, db_path: str = DEFAULT_MEMORY_DB_PATH):
        self._path = Path(db_path).expanduser()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            str(self._path), check_same_thread=False, isolation_level=None
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_scopes (
                tenant_key TEXT NOT NULL,
                scope_type TEXT NOT NULL,
                scope_id TEXT NOT NULL,
                store_id TEXT NOT NULL,
                created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
                PRIMARY KEY (scope_type, scope_id)
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS session_memory_scopes (
                session_id TEXT PRIMARY KEY,
                tenant_key TEXT NOT NULL,
                scope_type TEXT NOT NULL,
                scope_id TEXT NOT NULL,
                store_id TEXT NOT NULL,
                created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
            )
            """
        )
        self._protect_files()

    def _protect_files(self) -> None:
        for path in (
            self._path,
            Path(f"{self._path}-wal"),
            Path(f"{self._path}-shm"),
        ):
            if path.exists():
                path.chmod(0o600)

    def get_memory_store(
        self, tenant_key: str, scope_type: str, scope_id: str
    ) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT store_id FROM memory_scopes
                WHERE scope_type = ? AND scope_id = ?
                """,
                (scope_type, scope_id),
            ).fetchone()
        return row[0] if row else None

    def save_memory_store(
        self, tenant_key: str, scope_type: str, scope_id: str, store_id: str
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO memory_scopes (
                    tenant_key, scope_type, scope_id, store_id
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(scope_type, scope_id) DO UPDATE SET
                    tenant_key=excluded.tenant_key,
                    store_id=excluded.store_id
                """,
                (tenant_key, scope_type, scope_id, store_id),
            )

    def save_session_memory_scope(
        self,
        session_id: str,
        tenant_key: str,
        scope_type: str,
        scope_id: str,
        store_id: str,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                REPLACE INTO session_memory_scopes (
                    session_id, tenant_key, scope_type, scope_id, store_id
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, tenant_key, scope_type, scope_id, store_id),
            )

    def get_session_memory_scope(self, session_id: str) -> Optional[dict[str, str]]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT tenant_key, scope_type, scope_id, store_id
                FROM session_memory_scopes WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "tenant_key": row[0],
            "scope_type": row[1],
            "scope_id": row[2],
            "store_id": row[3],
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def build_memory_custom_tools() -> list[dict]:
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


class ScopedMemoryManager:
    """以 Session 持久化绑定为权限边界，代理 Memory Store API。

    可选注入数据面解析器（P3）：
      - ``scope_resolver(message) -> MemoryScope``：群作用域改用 project_id 做 scope_id，
        实现「同项目多群共享群记忆」；未注入时退回 ``MemoryScope.from_message``（一群一记忆）。
      - ``writable_resolver(scope) -> tuple[str, ...]``：该作用域允许写入的分类子集；
        ``memory_upsert`` / ``memory_forget`` 越权时返回结构化错误。未注入时可写=全部分类。
    读取（list/get）永远允许全部分类，只有写入受可写子集约束。
    """

    def __init__(
        self,
        ark: ArkClient,
        store: object,
        *,
        scope_resolver: Optional[Callable[[IncomingMessage], "MemoryScope"]] = None,
        writable_resolver: Optional[Callable[["MemoryScope"], tuple[str, ...]]] = None,
        store_id_resolver: Optional[Callable[["MemoryScope"], Optional[str]]] = None,
    ):
        self._ark = ark
        self._store = store
        self._locks: dict[str, asyncio.Lock] = {}
        self._scope_resolver = scope_resolver
        self._writable_resolver = writable_resolver
        self._store_id_resolver = store_id_resolver

    def _scope(self, message: IncomingMessage) -> "MemoryScope":
        """统一作用域解析入口：优先用注入的数据面解析器，否则退回内置规则。"""
        if self._scope_resolver is not None:
            return self._scope_resolver(message)
        return MemoryScope.from_message(message)

    def _writable_categories(self, scope: "MemoryScope") -> tuple[str, ...]:
        if self._writable_resolver is not None:
            return self._writable_resolver(scope)
        return MEMORY_CATEGORIES

    async def resources_for_message(
        self, message: IncomingMessage
    ) -> tuple[MemoryScope, str, list[dict]]:
        scope = self._scope(message)
        store_id = await self._ensure_store(
            scope, legacy_scope=MemoryScope.legacy_open_id_scope(message)
        )
        instructions = (
            USER_MEMORY_INSTRUCTIONS
            if scope.scope_type == "user"
            else GROUP_MEMORY_INSTRUCTIONS
        )
        return scope, store_id, [
            {
                "type": "memory_store",
                "memory_store_id": store_id,
                "instructions": instructions,
            }
        ]

    def bind_session(
        self, session_id: str, scope: MemoryScope, store_id: str
    ) -> None:
        self._store.save_session_memory_scope(
            session_id,
            scope.tenant_key,
            scope.scope_type,
            scope.scope_id,
            store_id,
        )

    def session_matches_message(
        self, session_id: str, message: IncomingMessage
    ) -> bool:
        binding = self._store.get_session_memory_scope(session_id)
        if not binding:
            return False
        expected = self._scope(message)
        identity_matches = (
            binding["scope_type"],
            binding["scope_id"],
        ) == (expected.scope_type, expected.scope_id)
        current_store = self._store.get_memory_store(
            expected.tenant_key, expected.scope_type, expected.scope_id
        )
        return identity_matches and binding["store_id"] == current_store

    async def always_apply_context(self, message: IncomingMessage) -> str:
        """把群级行为约定显式注入每轮输入，避免依赖 Memory Store 的概率性语义召回。"""
        if message.chat_type != "group":
            return ""
        scope = self._scope(message)
        store_id = self._store.get_memory_store(
            scope.tenant_key, scope.scope_type, scope.scope_id
        )
        if not store_id:
            return ""
        items = await self._ark.list_memories(
            store_id, "/conventions/", depth=2
        )
        lines: list[str] = []
        used_bytes = 0
        for item in sorted(items, key=lambda value: value.get("path", "")):
            if item.get("type") == "directory" or not item.get("id"):
                continue
            detail = await self._ark.get_memory(store_id, item["id"])
            content = str(detail.get("content") or "").strip()
            if not content:
                continue
            line = f"- {content}"
            line_bytes = len(line.encode("utf-8"))
            if lines and used_bytes + line_bytes > MAX_ALWAYS_APPLY_BYTES:
                break
            if line_bytes > MAX_ALWAYS_APPLY_BYTES:
                line = line.encode("utf-8")[:MAX_ALWAYS_APPLY_BYTES].decode(
                    "utf-8", errors="ignore"
                )
                line_bytes = len(line.encode("utf-8"))
            lines.append(line)
            used_bytes += line_bytes
            if len(lines) >= MAX_ALWAYS_APPLY_CONVENTIONS:
                break
        if not lines:
            return ""
        return "【群共享约定（必须遵守）】\n" + "\n".join(lines)

    async def handle_tool(
        self, session_id: str, name: str, arguments: dict
    ) -> tuple[str, bool]:
        binding = self._store.get_session_memory_scope(session_id)
        if not binding:
            return self._error("MEMORY_SCOPE_MISSING", "当前 Session 未绑定记忆作用域")
        scope = MemoryScope(
            binding["tenant_key"], binding["scope_type"], binding["scope_id"]
        )
        # 写入类工具受可写子集约束；读取（list/get）不校验，读=全部分类。
        if name in ("memory_upsert", "memory_forget"):
            denial = self._check_writable(scope, arguments)
            if denial is not None:
                return denial
        lock = self._locks.setdefault(scope.lock_key(), asyncio.Lock())
        try:
            async with lock:
                if name == "memory_list":
                    result = await self._list(binding["store_id"], arguments)
                elif name == "memory_get":
                    result = await self._get(binding["store_id"], arguments)
                elif name == "memory_upsert":
                    result = await self._upsert(binding["store_id"], arguments)
                elif name == "memory_forget":
                    result = await self._forget(binding["store_id"], arguments)
                else:
                    return self._error("UNKNOWN_MEMORY_TOOL", f"不支持的记忆工具：{name}")
        except (TypeError, ValueError) as error:
            return self._error("INVALID_ARGUMENT", str(error))
        except Exception as error:  # noqa: BLE001 - 工具错误必须回传给 Agent，不能卡住 Session
            return self._error("MEMORY_API_ERROR", str(error)[:300])
        result["scope"] = scope.scope_type
        return json.dumps(result, ensure_ascii=False), False

    def _check_writable(
        self, scope: "MemoryScope", arguments: dict
    ) -> Optional[tuple[str, bool]]:
        """写入前校验 category ∈ 可写子集；越权返回结构化错误，否则返回 None 放行。"""
        writable = self._writable_categories(scope)
        raw = arguments.get("category")
        # 无效 category 交给下游 _category 抛 INVALID_ARGUMENT，这里只拦「合法但越权」。
        if not isinstance(raw, str) or raw not in MEMORY_CATEGORIES:
            return None
        if raw in writable:
            return None
        return self._error(
            "MEMORY_CATEGORY_READONLY",
            f"分类 {raw} 在当前作用域为只读，可写分类：{', '.join(writable) or '（无）'}",
        )

    async def _ensure_store(
        self, scope: MemoryScope, *, legacy_scope: Optional[MemoryScope] = None
    ) -> str:
        lock = self._locks.setdefault(scope.lock_key(), asyncio.Lock())
        async with lock:
            existing = self._store.get_memory_store(
                scope.tenant_key, scope.scope_type, scope.scope_id
            )
            if existing:
                return existing
            # 控制面已为项目建好共享 Store 时，直接采用并回填映射，保持控制/数据面一致，
            # 不再重复懒建（未注入或返回空时退回下面的 legacy / 懒建路径）。
            if self._store_id_resolver is not None:
                provisioned = self._store_id_resolver(scope)
                if provisioned:
                    self._store.save_memory_store(
                        scope.tenant_key,
                        scope.scope_type,
                        scope.scope_id,
                        provisioned,
                    )
                    return provisioned
            if legacy_scope is not None:
                legacy_store = self._store.get_memory_store(
                    legacy_scope.tenant_key,
                    legacy_scope.scope_type,
                    legacy_scope.scope_id,
                )
                if legacy_store:
                    self._store.save_memory_store(
                        scope.tenant_key,
                        scope.scope_type,
                        scope.scope_id,
                        legacy_store,
                    )
                    return legacy_store
            digest = hashlib.sha256(scope.lock_key().encode()).hexdigest()[:16]
            label = "user" if scope.scope_type == "user" else "group"
            store_id = await self._ark.create_memory_store(
                f"feishu-{label}-{digest}",
                (
                    "Private long-term memory for one Feishu user."
                    if scope.scope_type == "user"
                    else "Shared long-term memory for one Feishu group and all its topics."
                ),
            )
            self._store.save_memory_store(
                scope.tenant_key, scope.scope_type, scope.scope_id, store_id
            )
            return store_id

    async def _list(self, store_id: str, arguments: dict) -> dict:
        raw_category = arguments.get("category")
        category = self._category(raw_category) if raw_category else ""
        prefix = f"/{category}/" if category else "/"
        items = await self._ark.list_memories(store_id, prefix, depth=2)
        paths = sorted(
            item["path"]
            for item in items
            if item.get("type") != "directory" and item.get("path", "").startswith(prefix)
        )
        return {"ok": True, "items": paths[:MAX_LIST_ITEMS], "truncated": len(paths) > MAX_LIST_ITEMS}

    async def _get(self, store_id: str, arguments: dict) -> dict:
        path = self._path(arguments)
        item = await self._find(store_id, path)
        if not item:
            return {"ok": True, "found": False, "path": path}
        memory = await self._ark.get_memory(store_id, item["id"])
        return {
            "ok": True,
            "found": True,
            "path": path,
            "content": memory["content"],
        }

    async def _upsert(self, store_id: str, arguments: dict) -> dict:
        path = self._path(arguments)
        content = arguments.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("content 不能为空")
        content = content.strip()
        if len(content.encode("utf-8")) > MAX_MEMORY_BYTES:
            raise ValueError(f"content 超过 {MAX_MEMORY_BYTES} bytes")
        item = await self._find(store_id, path)
        if item:
            await self._ark.update_memory(
                store_id, item["id"], path=path, content=content
            )
            action = "updated"
        else:
            await self._ark.create_memory(store_id, path, content)
            action = "created"
        return {"ok": True, "action": action, "path": path, "content": content}

    async def _forget(self, store_id: str, arguments: dict) -> dict:
        path = self._path(arguments)
        reason = arguments.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("删除记忆必须提供用户明确要求删除的 reason")
        item = await self._find(store_id, path)
        if not item:
            return {"ok": True, "action": "not_found", "path": path}
        await self._ark.delete_memory(store_id, item["id"])
        return {"ok": True, "action": "deleted", "path": path}

    async def _find(self, store_id: str, path: str) -> Optional[dict]:
        items = await self._ark.list_memories(store_id, path, depth=1)
        return next((item for item in items if item.get("path") == path), None)

    @staticmethod
    def _category(value: object) -> str:
        if not isinstance(value, str) or value not in MEMORY_CATEGORIES:
            raise ValueError(
                f"category 必须是：{', '.join(MEMORY_CATEGORIES)}"
            )
        return value

    @classmethod
    def _path(cls, arguments: dict) -> str:
        category = cls._category(arguments.get("category"))
        value = arguments.get("key")
        if not isinstance(value, str):
            raise ValueError("key 必须是字符串")
        normalized = unicodedata.normalize("NFKC", value).strip().lower()
        normalized = re.sub(r"\s+", "-", normalized)
        normalized = re.sub(r"[^\w.-]+", "-", normalized, flags=re.UNICODE)
        normalized = normalized.strip("._-")[:80]
        if not normalized or normalized in {".", ".."}:
            raise ValueError("key 不能为空或包含非法路径")
        return f"/{category}/{normalized}.md"

    @staticmethod
    def _error(code: str, message: str) -> tuple[str, bool]:
        return json.dumps(
            {"ok": False, "error": {"code": code, "message": message}},
            ensure_ascii=False,
        ), True
