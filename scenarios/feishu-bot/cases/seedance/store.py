"""case 私有状态：话题、卡片、参考素材、挂载快照、上下文与产物投递记录。"""
from __future__ import annotations

import json

from arkagent.feishu import ResourceRef
from arkagent.store import ConversationKey, GatewayStore


class SeedanceStore(GatewayStore):
    def __init__(self, path: str):
        super().__init__(path)
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS seedance_inputs (
                conversation_key TEXT NOT NULL, resource_key TEXT NOT NULL,
                descriptor TEXT NOT NULL, file_id TEXT NOT NULL DEFAULT '',
                mount_path TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (conversation_key, resource_key)
            );
            CREATE TABLE IF NOT EXISTS seedance_snapshots (
                session_id TEXT PRIMARY KEY, resource_keys TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS seedance_turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_key TEXT NOT NULL, request TEXT NOT NULL, reply TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS seedance_delivered (
                session_id TEXT NOT NULL, file_id TEXT NOT NULL,
                PRIMARY KEY (session_id, file_id)
            );
            CREATE TABLE IF NOT EXISTS seedance_topics (
                chat_id TEXT NOT NULL, alias TEXT NOT NULL, topic_root TEXT NOT NULL,
                PRIMARY KEY (chat_id, alias)
            );
            CREATE TABLE IF NOT EXISTS seedance_cards (
                conversation_key TEXT PRIMARY KEY, state_json TEXT NOT NULL
            );
            """
        )
        self._db.commit()

    def topic(self, chat_id: str, *aliases: str) -> str:
        with self._lock:
            for alias in aliases:
                if not alias:
                    continue
                row = self._db.execute(
                    "SELECT topic_root FROM seedance_topics WHERE chat_id = ? AND alias = ?",
                    (chat_id, alias),
                ).fetchone()
                if row:
                    return row[0]
        return ""

    def save_topic(self, chat_id: str, root: str, *aliases: str) -> None:
        with self._lock:
            for alias in {root, *aliases} - {""}:
                self._db.execute(
                    "INSERT OR IGNORE INTO seedance_topics VALUES (?, ?, ?)",
                    (chat_id, alias, root),
                )
            self._db.commit()

    def card(self, key: ConversationKey) -> dict:
        with self._lock:
            row = self._db.execute(
                "SELECT state_json FROM seedance_cards WHERE conversation_key = ?",
                (self.conversation_key(key),),
            ).fetchone()
        return json.loads(row[0]) if row else {}

    def save_card(self, key: ConversationKey, state: dict) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO seedance_cards VALUES (?, ?)",
                (self.conversation_key(key), json.dumps(state, ensure_ascii=False)),
            )
            self._db.commit()

    def active_cards(self) -> list[tuple[ConversationKey, str, dict]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT c.conversation_key, c.session_id, s.state_json FROM conversations c "
                "JOIN seedance_cards s ON s.conversation_key = c.conversation_key"
            ).fetchall()
        result = []
        for encoded_key, session_id, encoded in rows:
            state = json.loads(encoded)
            if (
                state.get("phase") not in ("running", "delivering", "timed_out")
                and not (state.get("phase") == "failed" and not state.get("turn_id"))
            ):
                continue
            chat, thread, user = encoded_key.split(":", 2)
            key = ConversationKey("", chat, "" if thread == "-" else thread, user)
            result.append((key, session_id, state))
        return result
    def add_input(self, key: ConversationKey, ref: ResourceRef) -> None:
        descriptor = json.dumps(ref.__dict__, ensure_ascii=False)
        resource_key = f"{ref.message_id}:{ref.file_key}"
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO seedance_inputs "
                "(conversation_key, resource_key, descriptor) VALUES (?, ?, ?)",
                (self.conversation_key(key), resource_key, descriptor),
            )
            self._db.commit()

    def inputs(self, key: ConversationKey) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT resource_key, descriptor, file_id, mount_path "
                "FROM seedance_inputs WHERE conversation_key = ? ORDER BY rowid",
                (self.conversation_key(key),),
            ).fetchall()
        return [
            {"key": r[0], "ref": ResourceRef(**json.loads(r[1])), "file_id": r[2], "mount_path": r[3]}
            for r in rows
        ]

    def save_input(self, key: ConversationKey, resource_key: str, file_id: str, path: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE seedance_inputs SET file_id = ?, mount_path = ? "
                "WHERE conversation_key = ? AND resource_key = ?",
                (file_id, path, self.conversation_key(key), resource_key),
            )
            self._db.commit()

    def snapshot(self, session_id: str) -> set[str]:
        with self._lock:
            row = self._db.execute(
                "SELECT resource_keys FROM seedance_snapshots WHERE session_id = ?", (session_id,)
            ).fetchone()
        return set(json.loads(row[0])) if row else set()

    def save_snapshot(self, session_id: str, resource_keys: set[str]) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO seedance_snapshots VALUES (?, ?)",
                (session_id, json.dumps(sorted(resource_keys))),
            )
            self._db.commit()

    def save_turn(self, key: ConversationKey, request: str, reply: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO seedance_turns (conversation_key, request, reply) VALUES (?, ?, ?)",
                (self.conversation_key(key), request[:6000], reply[:6000]),
            )
            self._db.commit()

    def context(self, key: ConversationKey) -> str:
        with self._lock:
            rows = self._db.execute(
                "SELECT request, reply FROM seedance_turns "
                "WHERE conversation_key = ? ORDER BY id DESC LIMIT 3",
                (self.conversation_key(key),),
            ).fetchall()
        return "\n\n".join(f"用户：{r[0]}\n助手：{r[1]}" for r in reversed(rows))

    def delivered(self, session_id: str, file_id: str) -> bool:
        with self._lock:
            row = self._db.execute(
                "SELECT 1 FROM seedance_delivered WHERE session_id = ? AND file_id = ?",
                (session_id, file_id),
            ).fetchone()
        return row is not None

    def mark_delivered(self, session_id: str, file_id: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO seedance_delivered VALUES (?, ?)", (session_id, file_id)
            )
            self._db.commit()

    def reset_session(self, key: ConversationKey) -> None:
        super().reset_session(key)
        with self._lock:
            for table in ("seedance_inputs", "seedance_turns"):
                self._db.execute(
                    f"DELETE FROM {table} WHERE conversation_key = ?", (self.conversation_key(key),)
                )
            self._db.commit()
