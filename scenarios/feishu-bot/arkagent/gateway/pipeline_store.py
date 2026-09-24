"""topic6 pipeline 长任务映射库(飞书 ↔ 方舟 MA Session)。

用途:把飞书三元组会话键 ``(chat_id, thread_id, user_open_id)`` 映射到方舟 MA Session,
并记录当前 pipeline 所在 phase、mode、project_dir、HC 卡点历史。用于:

  - HC 卡片回调时,反查 ``ma_session_id`` 以调 ``POST /sessions/{id}/events`` 续跑
  - 会话意外中断后,断点恢复(取 ``status.current_phase``)
  - 运营侧看板查看长任务状态

物理独立于 config_store / memory / conversation 库,单独一个 SQLite:
``data/topic6_pipeline.db``(可用 ``TOPIC6_PIPELINE_DB_PATH`` 覆盖)。

连接风格对齐 config_store.py:``check_same_thread=False`` + WAL + ``chmod 0o600``,
JSON 字段用 TEXT 序列化。
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

DEFAULT_PIPELINE_DB_PATH = str(
    Path(__file__).resolve().parents[3] / "data" / "topic6_pipeline.db"
)

# 生命周期状态。
STATUS_RUNNING = "running"
STATUS_WAIT_HC = "wait_hc"
STATUS_DONE = "done"
STATUS_FAILED = "failed"

# HC 卡点标识。
HC_KINDS = ("HC1", "HC2", "HC3")


def _new_job_id() -> str:
    return f"job_{uuid.uuid4().hex[:12]}"


def _now() -> int:
    return int(time.time())


def _dumps(value: object) -> str:
    return json.dumps(value or {}, ensure_ascii=False)


def _loads(value: Optional[str], default: object) -> object:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


# ---- 实体模型 --------------------------------------------------------------


@dataclass
class PipelineJob:
    """一次 topic6 pipeline 运行 ⇔ 一个方舟 MA Session。"""

    job_id: str
    chat_id: str
    thread_id: str
    user_open_id: str
    ma_session_id: str
    mode: str  # test | full
    project_dir: str
    current_phase: str = "A"  # A / B / C1 / C2 / D / HC1 / FULL / HC2 / E / F / HC3 / G / H
    status: str = STATUS_RUNNING
    started_at: int = field(default_factory=_now)
    updated_at: int = field(default_factory=_now)
    finished_at: Optional[int] = None
    last_error: str = ""
    online_url: str = ""  # Phase H 妙搭产物
    progress_card_message_id: str = ""  # 进度卡片飞书 message_id;用于 patch 覆写


@dataclass
class HcEvent:
    """一次 HC 卡点交互记录(gateway 侧审计用)。"""

    id: int
    job_id: str
    hc_kind: str  # HC1 | HC2 | HC3
    payload: dict  # Agent 结构化输出(distribution_summary / feishu_doc_url 等)
    card_message_id: str = ""  # 飞书卡片 message_id,便于事后 patch
    user_decision: str = ""  # pass | reject | remark
    user_note: str = ""
    created_at: int = 0
    resolved_at: Optional[int] = None


# ---- DAO -------------------------------------------------------------------


class PipelineStore:
    """topic6 长任务映射表 DAO。"""

    def __init__(self, db_path: Optional[str] = None):
        path = db_path or os.environ.get("TOPIC6_PIPELINE_DB_PATH") or DEFAULT_PIPELINE_DB_PATH
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
            CREATE TABLE IF NOT EXISTS pipeline_jobs (
                job_id TEXT PRIMARY KEY,
                chat_id TEXT NOT NULL,
                thread_id TEXT NOT NULL DEFAULT '',
                user_open_id TEXT NOT NULL,
                ma_session_id TEXT NOT NULL,
                mode TEXT NOT NULL,
                project_dir TEXT NOT NULL,
                current_phase TEXT NOT NULL DEFAULT 'A',
                status TEXT NOT NULL DEFAULT 'running',
                started_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                finished_at INTEGER,
                last_error TEXT NOT NULL DEFAULT '',
                online_url TEXT NOT NULL DEFAULT '',
                progress_card_message_id TEXT NOT NULL DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_pipeline_jobs_session_key
                ON pipeline_jobs(chat_id, thread_id, user_open_id, status);

            CREATE INDEX IF NOT EXISTS idx_pipeline_jobs_ma_session
                ON pipeline_jobs(ma_session_id);

            CREATE TABLE IF NOT EXISTS pipeline_hc_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                hc_kind TEXT NOT NULL,
                payload TEXT NOT NULL DEFAULT '{}',
                card_message_id TEXT NOT NULL DEFAULT '',
                user_decision TEXT NOT NULL DEFAULT '',
                user_note TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL,
                resolved_at INTEGER,
                FOREIGN KEY (job_id) REFERENCES pipeline_jobs(job_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_hc_events_job
                ON pipeline_hc_events(job_id, hc_kind);

            CREATE INDEX IF NOT EXISTS idx_hc_events_card
                ON pipeline_hc_events(card_message_id);
            """
        )
        # 老库补列:progress_card_message_id 是后加的,SQLite 没有 IF NOT EXISTS 语法。
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(pipeline_jobs)").fetchall()}
        if "progress_card_message_id" not in cols:
            self._conn.execute(
                "ALTER TABLE pipeline_jobs ADD COLUMN progress_card_message_id TEXT NOT NULL DEFAULT ''"
            )

    def _protect_files(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            path = Path(f"{self._path}{suffix}")
            if path.exists():
                path.chmod(0o600)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- pipeline_jobs -----------------------------------------------------

    def create_job(
        self,
        chat_id: str,
        thread_id: str,
        user_open_id: str,
        ma_session_id: str,
        mode: str,
        project_dir: str,
    ) -> PipelineJob:
        job = PipelineJob(
            job_id=_new_job_id(),
            chat_id=chat_id,
            thread_id=thread_id or "",
            user_open_id=user_open_id,
            ma_session_id=ma_session_id,
            mode=mode,
            project_dir=project_dir,
        )
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO pipeline_jobs
                    (job_id, chat_id, thread_id, user_open_id, ma_session_id,
                     mode, project_dir, current_phase, status,
                     started_at, updated_at, finished_at, last_error, online_url)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.job_id,
                    job.chat_id,
                    job.thread_id,
                    job.user_open_id,
                    job.ma_session_id,
                    job.mode,
                    job.project_dir,
                    job.current_phase,
                    job.status,
                    job.started_at,
                    job.updated_at,
                    job.finished_at,
                    job.last_error,
                    job.online_url,
                ),
            )
        return job

    def get_job(self, job_id: str) -> Optional[PipelineJob]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM pipeline_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return self._row_to_job(row) if row else None

    def get_active_job_by_session_key(
        self, chat_id: str, thread_id: str, user_open_id: str
    ) -> Optional[PipelineJob]:
        """会话键唯一活跃任务;完成/失败的历史任务不返回。"""
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM pipeline_jobs
                WHERE chat_id = ? AND thread_id = ? AND user_open_id = ?
                  AND status IN (?, ?)
                ORDER BY started_at DESC LIMIT 1
                """,
                (chat_id, thread_id or "", user_open_id, STATUS_RUNNING, STATUS_WAIT_HC),
            ).fetchone()
        return self._row_to_job(row) if row else None

    def get_job_by_ma_session(self, ma_session_id: str) -> Optional[PipelineJob]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM pipeline_jobs WHERE ma_session_id = ?",
                (ma_session_id,),
            ).fetchone()
        return self._row_to_job(row) if row else None

    def list_jobs(
        self, status: Optional[str] = None, limit: int = 50
    ) -> list[PipelineJob]:
        with self._lock:
            if status:
                rows = self._conn.execute(
                    "SELECT * FROM pipeline_jobs WHERE status = ? "
                    "ORDER BY started_at DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM pipeline_jobs ORDER BY started_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [self._row_to_job(r) for r in rows]

    def update_phase(self, job_id: str, current_phase: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE pipeline_jobs SET current_phase = ?, updated_at = ? WHERE job_id = ?",
                (current_phase, _now(), job_id),
            )

    def mark_wait_hc(self, job_id: str, hc_kind: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE pipeline_jobs SET status = ?, current_phase = ?, updated_at = ? "
                "WHERE job_id = ?",
                (STATUS_WAIT_HC, hc_kind, _now(), job_id),
            )

    def resume_running(self, job_id: str, next_phase: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE pipeline_jobs SET status = ?, current_phase = ?, updated_at = ? "
                "WHERE job_id = ?",
                (STATUS_RUNNING, next_phase, _now(), job_id),
            )

    def mark_done(self, job_id: str, online_url: str = "") -> None:
        now = _now()
        with self._lock:
            self._conn.execute(
                "UPDATE pipeline_jobs SET status = ?, finished_at = ?, updated_at = ?, "
                "online_url = COALESCE(NULLIF(?, ''), online_url) WHERE job_id = ?",
                (STATUS_DONE, now, now, online_url, job_id),
            )

    def mark_failed(self, job_id: str, error: str) -> None:
        now = _now()
        with self._lock:
            self._conn.execute(
                "UPDATE pipeline_jobs SET status = ?, finished_at = ?, updated_at = ?, "
                "last_error = ? WHERE job_id = ?",
                (STATUS_FAILED, now, now, error, job_id),
            )

    def set_progress_card_message_id(self, job_id: str, message_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE pipeline_jobs SET progress_card_message_id = ?, updated_at = ? "
                "WHERE job_id = ?",
                (message_id or "", _now(), job_id),
            )

    # ---- hc_events ---------------------------------------------------------

    def append_hc_event(
        self,
        job_id: str,
        hc_kind: str,
        payload: dict,
        card_message_id: str = "",
    ) -> int:
        if hc_kind not in HC_KINDS:
            raise ValueError(f"unknown hc_kind={hc_kind}")
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO pipeline_hc_events
                    (job_id, hc_kind, payload, card_message_id, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (job_id, hc_kind, _dumps(payload), card_message_id, _now()),
            )
            return int(cur.lastrowid)

    def resolve_hc_event(
        self,
        event_id: int,
        user_decision: str,
        user_note: str = "",
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                UPDATE pipeline_hc_events
                SET user_decision = ?, user_note = ?, resolved_at = ?
                WHERE id = ?
                """,
                (user_decision, user_note, _now(), event_id),
            )

    def get_hc_event(self, event_id: int) -> Optional[HcEvent]:
        """按主键 id 反查一条 HC 记录。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM pipeline_hc_events WHERE id = ?", (event_id,)
            ).fetchone()
        return self._row_to_hc(row) if row else None

    def append_remark(self, event_id: int, note: str) -> None:
        """备注按钮先落一次说明,不 resolve;等用户补一句正文再唤醒 pipeline。"""
        with self._lock:
            self._conn.execute(
                "UPDATE pipeline_hc_events SET user_note = ? WHERE id = ?",
                (note, event_id),
            )

    def get_pending_hc_by_card(self, card_message_id: str) -> Optional[HcEvent]:
        """飞书卡片回调回来时,凭 card_message_id 反查未 resolve 的 HC 事件。"""
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM pipeline_hc_events
                WHERE card_message_id = ? AND resolved_at IS NULL
                ORDER BY created_at DESC LIMIT 1
                """,
                (card_message_id,),
            ).fetchone()
        return self._row_to_hc(row) if row else None

    def latest_hc_for_job(self, job_id: str, hc_kind: str) -> Optional[HcEvent]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM pipeline_hc_events
                WHERE job_id = ? AND hc_kind = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (job_id, hc_kind),
            ).fetchone()
        return self._row_to_hc(row) if row else None

    # ---- row -> dataclass --------------------------------------------------

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> PipelineJob:
        return PipelineJob(
            job_id=row["job_id"],
            chat_id=row["chat_id"],
            thread_id=row["thread_id"],
            user_open_id=row["user_open_id"],
            ma_session_id=row["ma_session_id"],
            mode=row["mode"],
            project_dir=row["project_dir"],
            current_phase=row["current_phase"],
            status=row["status"],
            started_at=row["started_at"],
            updated_at=row["updated_at"],
            finished_at=row["finished_at"],
            last_error=row["last_error"],
            online_url=row["online_url"],
            progress_card_message_id=(
                row["progress_card_message_id"]
                if "progress_card_message_id" in row.keys()
                else ""
            ),
        )

    @staticmethod
    def _row_to_hc(row: sqlite3.Row) -> HcEvent:
        return HcEvent(
            id=row["id"],
            job_id=row["job_id"],
            hc_kind=row["hc_kind"],
            payload=_loads(row["payload"], {}),
            card_message_id=row["card_message_id"],
            user_decision=row["user_decision"],
            user_note=row["user_note"],
            created_at=row["created_at"],
            resolved_at=row["resolved_at"],
        )
