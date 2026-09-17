import json
import sys
from pathlib import Path

_GROUP_BOT_DIR = Path(__file__).resolve().parents[1] / "cases" / "digital-employee"
if str(_GROUP_BOT_DIR) not in sys.path:
    sys.path.insert(0, str(_GROUP_BOT_DIR))

import memory as group_memory  # noqa: E402
import shared  # noqa: E402
from arkagent.feishu import IncomingMessage  # noqa: E402


class FakeMemoryArk:
    def __init__(self):
        self.created_stores: list[tuple[str, str]] = []
        self.memories: dict[str, dict[str, dict]] = {}
        self.next_memory_id = 1

    async def create_memory_store(self, name: str, description: str) -> str:
        self.created_stores.append((name, description))
        store_id = f"store-{len(self.created_stores)}"
        self.memories[store_id] = {}
        return store_id

    async def list_memories(self, store_id: str, path_prefix="/", depth=2):
        del depth
        return [
            {"id": value["id"], "path": path, "type": "file"}
            for path, value in self.memories[store_id].items()
            if path.startswith(path_prefix)
        ]

    async def get_memory(self, store_id: str, memory_id: str):
        for path, value in self.memories[store_id].items():
            if value["id"] == memory_id:
                return {"id": memory_id, "path": path, "content": value["content"]}
        raise KeyError(memory_id)

    async def create_memory(self, store_id: str, path: str, content: str):
        memory_id = f"memory-{self.next_memory_id}"
        self.next_memory_id += 1
        self.memories[store_id][path] = {"id": memory_id, "content": content}
        return {"id": memory_id, "path": path, "content": content}

    async def update_memory(self, store_id: str, memory_id: str, **changes):
        for old_path, value in list(self.memories[store_id].items()):
            if value["id"] != memory_id:
                continue
            new_path = changes.get("path") or old_path
            new_content = changes.get("content", value["content"])
            del self.memories[store_id][old_path]
            self.memories[store_id][new_path] = {
                "id": memory_id,
                "content": new_content,
            }
            return {"id": memory_id, "path": new_path, "content": new_content}
        raise KeyError(memory_id)

    async def delete_memory(self, store_id: str, memory_id: str):
        for path, value in list(self.memories[store_id].items()):
            if value["id"] == memory_id:
                del self.memories[store_id][path]
                return
        raise KeyError(memory_id)


def _message(
    *,
    chat_type: str,
    chat_id: str,
    open_id: str,
    user_id: str = "",
    thread_id: str = "",
) -> IncomingMessage:
    return IncomingMessage(
        event_id="event",
        message_id="message",
        chat_id=chat_id,
        chat_type=chat_type,
        thread_id=thread_id,
        user_open_id=open_id,
        user_name="User",
        tenant_key="tenant",
        text="hello",
        mentioned_bot=chat_type != "p2p",
        user_id=user_id,
        create_time=1,
    )


async def test_direct_users_have_separate_stores():
    ark = FakeMemoryArk()
    store = shared.InMemorySessionMap()
    manager = group_memory.ScopedMemoryManager(ark, store)

    _, first_store, _ = await manager.resources_for_message(
        _message(chat_type="p2p", chat_id="dm-a", open_id="user-a")
    )
    _, second_store, _ = await manager.resources_for_message(
        _message(chat_type="p2p", chat_id="dm-b", open_id="user-b")
    )

    assert first_store != second_store
    assert len(ark.created_stores) == 2


async def test_direct_memory_uses_user_id_across_open_id_changes():
    ark = FakeMemoryArk()
    store = shared.InMemorySessionMap()
    manager = group_memory.ScopedMemoryManager(ark, store)

    first_scope, first_store, _ = await manager.resources_for_message(
        _message(
            chat_type="p2p",
            chat_id="dm-a",
            open_id="ou-old-app",
            user_id="u-stable",
        )
    )
    second_scope, second_store, _ = await manager.resources_for_message(
        _message(
            chat_type="p2p",
            chat_id="dm-b",
            open_id="ou-new-app",
            user_id="u-stable",
        )
    )

    assert first_scope.scope_id == second_scope.scope_id == "u-stable"
    assert first_store == second_store
    assert len(ark.created_stores) == 1


async def test_direct_memory_migrates_legacy_open_id_scope_to_user_id():
    ark = FakeMemoryArk()
    store = shared.InMemorySessionMap()
    manager = group_memory.ScopedMemoryManager(ark, store)
    store.save_memory_store("tenant", "user", "ou-legacy", "store-existing")

    scope, store_id, _ = await manager.resources_for_message(
        _message(
            chat_type="p2p",
            chat_id="dm-a",
            open_id="ou-legacy",
            user_id="u-stable",
        )
    )

    assert scope.scope_id == "u-stable"
    assert store_id == "store-existing"
    assert store.get_memory_store("tenant", "user", "u-stable") == "store-existing"
    assert ark.created_stores == []


async def test_group_topics_share_group_store_without_personal_store():
    ark = FakeMemoryArk()
    store = shared.InMemorySessionMap()
    manager = group_memory.ScopedMemoryManager(ark, store)
    first = _message(
        chat_type="group", chat_id="group-a", open_id="user-a", thread_id="topic-1"
    )
    second = _message(
        chat_type="group", chat_id="group-a", open_id="user-b", thread_id="topic-2"
    )

    first_scope, first_store, first_resources = await manager.resources_for_message(first)
    second_scope, second_store, _ = await manager.resources_for_message(second)

    assert first_scope.scope_type == second_scope.scope_type == "group"
    assert first_store == second_store
    assert len(ark.created_stores) == 1
    assert first_resources[0]["memory_store_id"] == first_store
    assert store.get_memory_store("tenant", "user", "user-a") is None


async def test_memory_tools_upsert_get_list_and_forget_in_bound_scope():
    ark = FakeMemoryArk()
    store = shared.InMemorySessionMap()
    manager = group_memory.ScopedMemoryManager(ark, store)
    message = _message(chat_type="group", chat_id="group-a", open_id="user-a")
    scope, store_id, _ = await manager.resources_for_message(message)
    manager.bind_session("session-1", scope, store_id)

    created, is_error = await manager.handle_tool(
        "session-1",
        "memory_upsert",
        {"category": "decisions", "key": "weekly-report", "content": "每周五提交"},
    )
    updated, _ = await manager.handle_tool(
        "session-1",
        "memory_upsert",
        {"category": "decisions", "key": "weekly-report", "content": "每周四提交"},
    )
    fetched, _ = await manager.handle_tool(
        "session-1",
        "memory_get",
        {"category": "decisions", "key": "weekly-report"},
    )
    listed, _ = await manager.handle_tool(
        "session-1", "memory_list", {"category": "decisions"}
    )
    deleted, _ = await manager.handle_tool(
        "session-1",
        "memory_forget",
        {
            "category": "decisions",
            "key": "weekly-report",
            "reason": "用户明确要求删除",
        },
    )

    assert is_error is False
    assert json.loads(created)["action"] == "created"
    assert json.loads(updated)["action"] == "updated"
    assert json.loads(fetched)["content"] == "每周四提交"
    assert json.loads(listed)["items"] == ["/decisions/weekly-report.md"]
    assert json.loads(deleted)["action"] == "deleted"


async def test_memory_tool_rejects_unbound_session_and_invalid_category():
    manager = group_memory.ScopedMemoryManager(
        FakeMemoryArk(), shared.InMemorySessionMap()
    )

    missing, missing_error = await manager.handle_tool(
        "unknown", "memory_list", {}
    )
    assert missing_error is True
    assert json.loads(missing)["error"]["code"] == "MEMORY_SCOPE_MISSING"

    message = _message(chat_type="p2p", chat_id="dm-a", open_id="user-a")
    scope, store_id, _ = await manager.resources_for_message(message)
    manager.bind_session("session-1", scope, store_id)
    invalid, invalid_error = await manager.handle_tool(
        "session-1",
        "memory_get",
        {"category": "other", "key": "../../secret"},
    )
    assert invalid_error is True
    assert json.loads(invalid)["error"]["code"] == "INVALID_ARGUMENT"


def test_sqlite_memory_state_is_shared_across_reopen(tmp_path):
    path = str(tmp_path / "memory.db")
    first = group_memory.SqliteMemoryState(path)
    first.save_memory_store("tenant", "group", "chat", "store-1")
    first.save_session_memory_scope(
        "session-1", "tenant", "group", "chat", "store-1"
    )
    first.close()

    second = group_memory.SqliteMemoryState(path)
    assert second.get_memory_store("tenant", "group", "chat") == "store-1"
    assert second.get_session_memory_scope("session-1") == {
        "tenant_key": "tenant",
        "scope_type": "group",
        "scope_id": "chat",
        "store_id": "store-1",
    }
    second.close()
