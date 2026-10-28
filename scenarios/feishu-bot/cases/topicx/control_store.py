"""Durable control-plane state for the unified Topicx gateway."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

ACTIVE_TASK_STATUSES = (
    "routing",
    "routed",
    "authorizing",
    "acquiring",
    "starting",
    "running",
    "wait_hc",
    "stopping",
    "reconcile_required",
)
TERMINAL_TASK_STATUSES = ("unsupported", "stopped", "done", "failed", "busy")
VALID_SCENARIOS = ("topic5", "topic6", "unsupported")
VALID_MODES = {
    "topic5": ("demo", "full"),
    "topic6": ("demo", "full", "skip_sampling"),
    "unsupported": ("",),
}


def _now() -> int:
    return int(time.time())


def _new_task_id() -> str:
    return f"task_{uuid.uuid4().hex}"


@dataclass(frozen=True)
class TaskRun:
    task_id: str
    feishu_event_id: str
    chat_id: str
    root_message_id: str
    topic_thread_id: str
    user_open_id: str
    request_text: str
    router_session_id: str
    business_session_id: str
    scenario: str
    mode: str
    route_reason: str
    business_job_id: str
    status: str
    last_error: str
    stop_requested_at: Optional[int]
    stop_requested_by: str
    created_at: int
    updated_at: int
    finished_at: Optional[int]


@dataclass(frozen=True)
class LeaseClaim:
    acquired: bool
    lease_token: str = ""
    owner_task_id: str = ""
    state: str = ""


class ControlStore:
    def __init__(self, db_path: str):
        self._path = Path(db_path).expanduser()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self._path), isolation_level=None, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._create_schema()
        self._protect_files()

    def _create_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS task_runs (
                task_id TEXT PRIMARY KEY,
                feishu_event_id TEXT NOT NULL UNIQUE,
                chat_id TEXT NOT NULL,
                root_message_id TEXT NOT NULL,
                topic_thread_id TEXT NOT NULL DEFAULT '',
                user_open_id TEXT NOT NULL,
                request_text TEXT NOT NULL DEFAULT '',
                router_session_id TEXT UNIQUE,
                business_session_id TEXT UNIQUE,
                scenario TEXT NOT NULL DEFAULT '',
                mode TEXT NOT NULL DEFAULT '',
                route_reason TEXT NOT NULL DEFAULT '',
                business_job_id TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'routing',
                last_error TEXT NOT NULL DEFAULT '',
                stop_requested_at INTEGER,
                stop_requested_by TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                finished_at INTEGER
            );

            CREATE INDEX IF NOT EXISTS idx_task_runs_root
                ON task_runs(chat_id, root_message_id);
            CREATE INDEX IF NOT EXISTS idx_task_runs_topic
                ON task_runs(chat_id, topic_thread_id);
            CREATE INDEX IF NOT EXISTS idx_task_runs_job
                ON task_runs(scenario, business_job_id);
            CREATE INDEX IF NOT EXISTS idx_task_runs_status
                ON task_runs(status, updated_at);

            CREATE TABLE IF NOT EXISTS topic6_lease (
                singleton_key TEXT PRIMARY KEY CHECK(singleton_key = 'topic6'),
                owner_task_id TEXT NOT NULL,
                owner_business_session_id TEXT NOT NULL DEFAULT '',
                owner_business_job_id TEXT NOT NULL DEFAULT '',
                lease_token TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('acquired', 'active', 'stopping')),
                acquired_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS control_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                task_id TEXT NOT NULL,
                correlation_id TEXT NOT NULL,
                causation_id TEXT NOT NULL DEFAULT '',
                payload TEXT NOT NULL DEFAULT '{}',
                created_at INTEGER NOT NULL,
                FOREIGN KEY(task_id) REFERENCES task_runs(task_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_control_events_task
                ON control_events(task_id, id);
            """
        )

    def _protect_files(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            path = Path(f"{self._path}{suffix}")
            if path.exists():
                path.chmod(0o600)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def create_or_get_task(
        self,
        *,
        feishu_event_id: str,
        chat_id: str,
        root_message_id: str,
        user_open_id: str,
        request_text: str,
        topic_thread_id: str = "",
    ) -> tuple[TaskRun, bool]:
        now = _now()
        task_id = _new_task_id()
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT OR IGNORE INTO task_runs (
                    task_id, feishu_event_id, chat_id, root_message_id,
                    topic_thread_id, user_open_id, request_text, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    feishu_event_id,
                    chat_id,
                    root_message_id,
                    topic_thread_id,
                    user_open_id,
                    request_text,
                    now,
                    now,
                ),
            )
            created = cursor.rowcount == 1
            row = self._conn.execute(
                "SELECT * FROM task_runs WHERE feishu_event_id = ?",
                (feishu_event_id,),
            ).fetchone()
            assert row is not None
            task = self._row_to_task(row)
            if created:
                self._append_event_locked(task.task_id, "task.created")
        self._protect_files()
        return task, created

    def get_task(self, task_id: str) -> Optional[TaskRun]:
        return self._fetch_one("task_id = ?", (task_id,))

    def get_task_by_router_session(self, session_id: str) -> Optional[TaskRun]:
        return self._fetch_one("router_session_id = ?", (session_id,))

    def get_task_by_business_session(self, session_id: str) -> Optional[TaskRun]:
        return self._fetch_one("business_session_id = ?", (session_id,))

    def get_task_by_job(self, scenario: str, job_id: str) -> Optional[TaskRun]:
        return self._fetch_one(
            "scenario = ? AND business_job_id = ?", (scenario, job_id)
        )

    def get_task_by_thread(
        self, chat_id: str, root_message_id: str, topic_thread_id: str = ""
    ) -> Optional[TaskRun]:
        if topic_thread_id:
            task = self._fetch_one(
                "chat_id = ? AND topic_thread_id = ?",
                (chat_id, topic_thread_id),
                order="updated_at DESC",
            )
            if task:
                return task
        return self._fetch_one(
            "chat_id = ? AND root_message_id = ?",
            (chat_id, root_message_id),
            order="updated_at DESC",
        )

    def list_recoverable_tasks(self) -> list[TaskRun]:
        marks = ",".join("?" for _ in ACTIVE_TASK_STATUSES)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM task_runs WHERE status IN ({marks}) ORDER BY created_at",
                ACTIVE_TASK_STATUSES,
            ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def bind_router_session(self, task_id: str, session_id: str) -> bool:
        return self._bind_once(
            task_id,
            "router_session_id",
            session_id,
            "router.session_created",
        )

    def bind_business_session(self, task_id: str, session_id: str) -> bool:
        return self._bind_once(
            task_id,
            "business_session_id",
            session_id,
            "business.session_created",
        )

    def bind_business_job(self, task_id: str, job_id: str) -> bool:
        return self._bind_once(
            task_id, "business_job_id", job_id, "business.job_created"
        )

    def commit_route_once(
        self, task_id: str, intent: str, mode: str, reason: str
    ) -> dict:
        if intent not in VALID_SCENARIOS:
            raise ValueError("invalid intent")
        normalized_mode = "" if intent == "unsupported" else mode
        if normalized_mode not in VALID_MODES[intent]:
            raise ValueError(f"invalid mode for {intent}")
        reason = reason.strip()[:100]
        status = "unsupported" if intent == "unsupported" else "routed"
        now = _now()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT * FROM task_runs WHERE task_id = ?", (task_id,)
                ).fetchone()
                if row is None:
                    raise KeyError(task_id)
                existing = self._row_to_task(row)
                if existing.scenario:
                    same = (
                        existing.scenario == intent
                        and existing.mode == normalized_mode
                    )
                    self._conn.execute("COMMIT")
                    return {
                        "accepted": same,
                        "task_id": task_id,
                        "intent": existing.scenario,
                        "mode": existing.mode,
                        **({} if same else {"error": "route_already_committed"}),
                    }
                finished_at = now if status == "unsupported" else None
                self._conn.execute(
                    """
                    UPDATE task_runs
                    SET scenario = ?, mode = ?, route_reason = ?, status = ?,
                        updated_at = ?, finished_at = ?
                    WHERE task_id = ? AND scenario = ''
                    """,
                    (
                        intent,
                        normalized_mode,
                        reason,
                        status,
                        now,
                        finished_at,
                        task_id,
                    ),
                )
                self._append_event_locked(
                    task_id,
                    "route.committed",
                    payload={"intent": intent, "mode": normalized_mode},
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return {
            "accepted": True,
            "task_id": task_id,
            "intent": intent,
            "mode": normalized_mode,
        }

    def claim_dispatch(self, task_id: str, next_status: str = "starting") -> bool:
        return self.transition(task_id, ("routed",), next_status)

    def transition(
        self,
        task_id: str,
        expected: tuple[str, ...],
        status: str,
        *,
        last_error: str = "",
        event_type: str = "",
    ) -> bool:
        marks = ",".join("?" for _ in expected)
        now = _now()
        terminal = status in TERMINAL_TASK_STATUSES
        with self._lock:
            cursor = self._conn.execute(
                f"""
                UPDATE task_runs
                SET status = ?, last_error = ?, updated_at = ?,
                    finished_at = CASE WHEN ? THEN ? ELSE NULL END
                WHERE task_id = ? AND status IN ({marks})
                """,
                (
                    status,
                    last_error[:1000],
                    now,
                    int(terminal),
                    now,
                    task_id,
                    *expected,
                ),
            )
            if cursor.rowcount and event_type:
                self._append_event_locked(task_id, event_type)
            return cursor.rowcount == 1

    def clear_request_text(self, task_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE task_runs SET request_text = '', updated_at = ? WHERE task_id = ?",
                (_now(), task_id),
            )

    def set_topic_thread_id(self, task_id: str, thread_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE task_runs SET topic_thread_id = ?, updated_at = ? WHERE task_id = ?",
                (thread_id, _now(), task_id),
            )

    def acquire_topic6(self, task_id: str) -> LeaseClaim:
        token = uuid.uuid4().hex
        now = _now()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT * FROM topic6_lease WHERE singleton_key = 'topic6'"
                ).fetchone()
                if row:
                    self._conn.execute("COMMIT")
                    return LeaseClaim(
                        acquired=row["owner_task_id"] == task_id,
                        lease_token=row["lease_token"]
                        if row["owner_task_id"] == task_id
                        else "",
                        owner_task_id=row["owner_task_id"],
                        state=row["state"],
                    )
                self._conn.execute(
                    """
                    INSERT INTO topic6_lease (
                        singleton_key, owner_task_id, lease_token, state,
                        acquired_at, updated_at
                    ) VALUES ('topic6', ?, ?, 'acquired', ?, ?)
                    """,
                    (task_id, token, now, now),
                )
                self._append_event_locked(task_id, "lease.acquired")
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return LeaseClaim(True, token, task_id, "acquired")

    def activate_topic6(
        self, task_id: str, token: str, session_id: str, job_id: str
    ) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                """
                UPDATE topic6_lease
                SET owner_business_session_id = ?, owner_business_job_id = ?,
                    state = 'active', updated_at = ?
                WHERE singleton_key = 'topic6' AND owner_task_id = ?
                  AND lease_token = ? AND state IN ('acquired', 'active')
                """,
                (session_id, job_id, _now(), task_id, token),
            )
            if cursor.rowcount:
                self._append_event_locked(task_id, "lease.activated")
            else:
                self._append_event_locked(task_id, "lease.activate_rejected")
            return cursor.rowcount == 1

    def begin_stop(self, task_id: str, operator_open_id: str) -> bool:
        now = _now()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = self._conn.execute(
                    """
                    UPDATE task_runs
                    SET status = 'stopping', stop_requested_at = ?,
                        stop_requested_by = ?, updated_at = ?
                    WHERE task_id = ? AND status IN ('running', 'wait_hc')
                    """,
                    (now, operator_open_id, now, task_id),
                )
                if cursor.rowcount:
                    self._conn.execute(
                        """
                        UPDATE topic6_lease
                        SET state = 'stopping', updated_at = ?
                        WHERE owner_task_id = ? AND state IN ('acquired', 'active')
                        """,
                        (now, task_id),
                    )
                    self._append_event_locked(
                        task_id,
                        "stop.requested",
                        payload={"operator_open_id": operator_open_id},
                    )
                self._conn.execute("COMMIT")
                return cursor.rowcount == 1
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def release_topic6_if_owner(
        self, task_id: str, token: str, *, reason: str
    ) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                """
                DELETE FROM topic6_lease
                WHERE singleton_key = 'topic6' AND owner_task_id = ?
                  AND lease_token = ?
                """,
                (task_id, token),
            )
            event = "lease.released" if cursor.rowcount else "lease.release_rejected"
            self._append_event_locked(task_id, event, payload={"reason": reason})
            return cursor.rowcount == 1

    def get_topic6_lease(self) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM topic6_lease WHERE singleton_key = 'topic6'"
            ).fetchone()
        return dict(row) if row else None

    def reconcile_topic6_lease(self) -> Optional[TaskRun]:
        lease = self.get_topic6_lease()
        return self.get_task(lease["owner_task_id"]) if lease else None

    def append_event(
        self,
        task_id: str,
        event_type: str,
        *,
        causation_id: str = "",
        payload: Optional[dict] = None,
    ) -> None:
        with self._lock:
            self._append_event_locked(
                task_id,
                event_type,
                causation_id=causation_id,
                payload=payload,
            )

    def list_events(self, task_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM control_events WHERE task_id = ? ORDER BY id",
                (task_id,),
            ).fetchall()
        return [
            {
                **dict(row),
                "payload": json.loads(row["payload"] or "{}"),
            }
            for row in rows
        ]

    def _bind_once(
        self, task_id: str, column: str, value: str, event_type: str
    ) -> bool:
        if column not in {
            "router_session_id",
            "business_session_id",
            "business_job_id",
        }:
            raise ValueError(column)
        with self._lock:
            row = self._conn.execute(
                f"SELECT {column} FROM task_runs WHERE task_id = ?", (task_id,)
            ).fetchone()
            if row is None:
                raise KeyError(task_id)
            existing = str(row[column] or "")
            if existing:
                return existing == value
            cursor = self._conn.execute(
                f"""
                UPDATE task_runs SET {column} = ?, updated_at = ?
                WHERE task_id = ? AND ({column} IS NULL OR {column} = '')
                """,
                (value, _now(), task_id),
            )
            if cursor.rowcount:
                self._append_event_locked(
                    task_id, event_type, payload={column: value}
                )
            return cursor.rowcount == 1

    def _fetch_one(
        self, where: str, params: tuple, *, order: str = ""
    ) -> Optional[TaskRun]:
        order_sql = f" ORDER BY {order}" if order else ""
        with self._lock:
            row = self._conn.execute(
                f"SELECT * FROM task_runs WHERE {where}{order_sql} LIMIT 1",
                params,
            ).fetchone()
        return self._row_to_task(row) if row else None

    def _append_event_locked(
        self,
        task_id: str,
        event_type: str,
        *,
        causation_id: str = "",
        payload: Optional[dict] = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO control_events (
                event_type, task_id, correlation_id, causation_id, payload, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event_type,
                task_id,
                task_id,
                causation_id,
                json.dumps(payload or {}, ensure_ascii=False),
                _now(),
            ),
        )

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> TaskRun:
        return TaskRun(**dict(row))
