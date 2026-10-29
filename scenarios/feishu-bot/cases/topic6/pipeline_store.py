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
from typing import Callable, Optional

DEFAULT_PIPELINE_DB_PATH = str(
    Path(__file__).resolve().parents[3] / "data" / "topic6_pipeline.db"
)

# 生命周期状态。
STATUS_RUNNING = "running"
STATUS_WAIT_HC = "wait_hc"
STATUS_STOPPING = "stopping"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_STOPPED = "stopped"

FAILURE_SCOPE_RECOVERABLE = "recoverable"
FAILURE_SCOPE_SESSION = "session"
FAILURE_SCOPE_TASK = "task"
RETRY_STRATEGY_RESUME_SESSION = "resume_session"

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
    mode: str  # demo | full | skip_sampling
    project_dir: str
    current_phase: str = "A"  # A / B / C1 / C2 / D / HC1 / FULL / HC2 / E / F / HC3 / G / H
    status: str = STATUS_RUNNING
    started_at: int = field(default_factory=_now)
    updated_at: int = field(default_factory=_now)
    finished_at: Optional[int] = None
    last_error: str = ""
    online_url: str = ""  # Phase H 妙搭产物
    progress_card_message_id: str = ""  # 进度卡片飞书 message_id;用于 patch 覆写
    root_message_id: str = ""
    topic_thread_id: str = ""
    task_id: str = ""
    stop_requested_at: Optional[int] = None
    stop_requested_by: str = ""
    failure_scope: str = ""
    retry_strategy: str = ""


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
    operator_label: str = ""
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
        self._status_listener: Optional[Callable[[PipelineJob, str], None]] = None
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
                failure_scope TEXT NOT NULL DEFAULT '',
                retry_strategy TEXT NOT NULL DEFAULT '',
                online_url TEXT NOT NULL DEFAULT '',
                progress_card_message_id TEXT NOT NULL DEFAULT '',
                root_message_id TEXT NOT NULL DEFAULT '',
                topic_thread_id TEXT NOT NULL DEFAULT '',
                task_id TEXT NOT NULL DEFAULT '',
                stop_requested_at INTEGER,
                stop_requested_by TEXT NOT NULL DEFAULT ''
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
                operator_label TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL,
                resolved_at INTEGER,
                FOREIGN KEY (job_id) REFERENCES pipeline_jobs(job_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_hc_events_job
                ON pipeline_hc_events(job_id, hc_kind);

            CREATE INDEX IF NOT EXISTS idx_hc_events_card
                ON pipeline_hc_events(card_message_id);

            CREATE TABLE IF NOT EXISTS user_oauth (
                open_id       TEXT PRIMARY KEY,
                vault_id      TEXT NOT NULL,
                credential_id TEXT NOT NULL,
                refresh_token TEXT NOT NULL,
                expires_at    INTEGER NOT NULL,
                scopes        TEXT NOT NULL,
                updated_at    INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
            );
            """
        )
        # 老库补列:progress_card_message_id 是后加的,SQLite 没有 IF NOT EXISTS 语法。
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(pipeline_jobs)").fetchall()}
        if "progress_card_message_id" not in cols:
            self._conn.execute(
                "ALTER TABLE pipeline_jobs ADD COLUMN progress_card_message_id TEXT NOT NULL DEFAULT ''"
            )
        if "root_message_id" not in cols:
            self._conn.execute(
                "ALTER TABLE pipeline_jobs ADD COLUMN root_message_id TEXT NOT NULL DEFAULT ''"
            )
        if "topic_thread_id" not in cols:
            self._conn.execute(
                "ALTER TABLE pipeline_jobs ADD COLUMN topic_thread_id TEXT NOT NULL DEFAULT ''"
            )
        if "task_id" not in cols:
            self._conn.execute(
                "ALTER TABLE pipeline_jobs ADD COLUMN task_id TEXT NOT NULL DEFAULT ''"
            )
        if "stop_requested_at" not in cols:
            self._conn.execute(
                "ALTER TABLE pipeline_jobs ADD COLUMN stop_requested_at INTEGER"
            )
        if "stop_requested_by" not in cols:
            self._conn.execute(
                "ALTER TABLE pipeline_jobs ADD COLUMN stop_requested_by TEXT NOT NULL DEFAULT ''"
            )
        if "failure_scope" not in cols:
            self._conn.execute(
                "ALTER TABLE pipeline_jobs ADD COLUMN failure_scope TEXT NOT NULL DEFAULT ''"
            )
        if "retry_strategy" not in cols:
            self._conn.execute(
                "ALTER TABLE pipeline_jobs ADD COLUMN retry_strategy TEXT NOT NULL DEFAULT ''"
            )
        self._conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_pipeline_jobs_task_id
            ON pipeline_jobs(task_id) WHERE task_id <> ''
            """
        )
        hc_cols = {
            r[1] for r in self._conn.execute("PRAGMA table_info(pipeline_hc_events)").fetchall()
        }
        if "operator_label" not in hc_cols:
            self._conn.execute(
                "ALTER TABLE pipeline_hc_events ADD COLUMN operator_label TEXT NOT NULL DEFAULT ''"
            )

    def _protect_files(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            path = Path(f"{self._path}{suffix}")
            if path.exists():
                path.chmod(0o600)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def set_status_listener(
        self, listener: Optional[Callable[[PipelineJob, str], None]]
    ) -> None:
        self._status_listener = listener

    def _notify_status(self, job_id: str, status: str) -> None:
        if self._status_listener is None:
            return
        job = self.get_job(job_id)
        if job is not None:
            self._status_listener(job, status)

    # ---- user_oauth -------------------------------------------------------

    def get_user_oauth(self, open_id: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT vault_id, credential_id, refresh_token, expires_at, scopes
                FROM user_oauth WHERE open_id = ?
                """,
                (open_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "open_id": open_id,
            "vault_id": row[0],
            "credential_id": row[1],
            "refresh_token": row[2],
            "expires_at": int(row[3]),
            "scopes": tuple(filter(None, str(row[4]).split(" "))),
        }

    def save_user_oauth(
        self,
        open_id: str,
        vault_id: str,
        credential_id: str,
        refresh_token: str,
        expires_at: int,
        scopes: tuple[str, ...],
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO user_oauth (
                    open_id, vault_id, credential_id,
                    refresh_token, expires_at, scopes
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(open_id) DO UPDATE SET
                    vault_id=excluded.vault_id,
                    credential_id=excluded.credential_id,
                    refresh_token=excluded.refresh_token,
                    expires_at=excluded.expires_at,
                    scopes=excluded.scopes,
                    updated_at=strftime('%s', 'now')
                """,
                (
                    open_id,
                    vault_id,
                    credential_id,
                    refresh_token,
                    expires_at,
                    " ".join(scopes),
                ),
            )

    # ---- pipeline_jobs -----------------------------------------------------

    def create_job(
        self,
        chat_id: str,
        thread_id: str,
        user_open_id: str,
        ma_session_id: str,
        mode: str,
        project_dir: str,
        root_message_id: str = "",
        task_id: str = "",
    ) -> PipelineJob:
        if task_id:
            with self._lock:
                existing = self._conn.execute(
                    "SELECT * FROM pipeline_jobs WHERE task_id = ?", (task_id,)
                ).fetchone()
            if existing:
                return self._row_to_job(existing)
        job = PipelineJob(
            job_id=_new_job_id(),
            chat_id=chat_id,
            thread_id=thread_id or "",
            user_open_id=user_open_id,
            ma_session_id=ma_session_id,
            mode=mode,
            project_dir=project_dir,
            root_message_id=root_message_id,
            task_id=task_id,
        )
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO pipeline_jobs
                    (job_id, chat_id, thread_id, user_open_id, ma_session_id,
                     mode, project_dir, current_phase, status,
                     started_at, updated_at, finished_at, last_error, online_url,
                     root_message_id, task_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    job.root_message_id,
                    job.task_id,
                ),
            )
        return job

    def get_job(self, job_id: str) -> Optional[PipelineJob]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM pipeline_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return self._row_to_job(row) if row else None

    def get_job_by_task(self, task_id: str) -> Optional[PipelineJob]:
        if not task_id:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM pipeline_jobs WHERE task_id = ?", (task_id,)
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
                  AND status IN (?, ?, ?)
                ORDER BY started_at DESC LIMIT 1
                """,
                (
                    chat_id,
                    thread_id or "",
                    user_open_id,
                    STATUS_RUNNING,
                    STATUS_WAIT_HC,
                    STATUS_STOPPING,
                ),
            ).fetchone()
        return self._row_to_job(row) if row else None

    def get_resumable_job_by_session_key(
        self, chat_id: str, thread_id: str, user_open_id: str
    ) -> Optional[PipelineJob]:
        """会话键上最近一个“可续跑”的 job(供 /continue 用)。

        与 get_active_job_by_session_key 的区别:后者只认 running/wait_hc,拿不到
        已被本地收口成 failed 的 job。而 /continue 的主场景恰恰是“卡片已失败,
        但 MA Session 其实还 idle”(SSE 断线/HC 契约错/reject/cancel)——这些 job 已落到
        failed,必须把它们纳入候选,才能把误判为死的任务捞回来重订 SSE。stopped
        表示用户请求的不可逆停止，不得恢复。

        完成态(done)不返回:已出报告的任务没有续跑语义。
        """
        thread = thread_id or ""
        with self._lock:
            if thread:
                row = self._conn.execute(
                    """
                    SELECT * FROM pipeline_jobs
                    WHERE chat_id = ? AND user_open_id = ?
                      AND (thread_id = ? OR topic_thread_id = ?)
                      AND status IN (?, ?, ?)
                    ORDER BY updated_at DESC LIMIT 1
                    """,
                    (
                        chat_id,
                        user_open_id,
                        thread,
                        thread,
                        STATUS_RUNNING,
                        STATUS_WAIT_HC,
                        STATUS_FAILED,
                    ),
                ).fetchone()
            else:
                row = self._conn.execute(
                    """
                    SELECT * FROM pipeline_jobs
                    WHERE chat_id = ? AND user_open_id = ?
                      AND thread_id = '' AND topic_thread_id = ''
                      AND status IN (?, ?, ?)
                    ORDER BY updated_at DESC LIMIT 1
                    """,
                    (
                        chat_id,
                        user_open_id,
                        STATUS_RUNNING,
                        STATUS_WAIT_HC,
                        STATUS_FAILED,
                    ),
                ).fetchone()
        return self._row_to_job(row) if row else None

    def get_active_job(self) -> Optional[PipelineJob]:
        """返回整个 Topic6 Gateway 最新的活跃任务。"""
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM pipeline_jobs
                WHERE status IN (?, ?, ?)
                ORDER BY started_at DESC LIMIT 1
                """,
                (STATUS_RUNNING, STATUS_WAIT_HC, STATUS_STOPPING),
            ).fetchone()
        return self._row_to_job(row) if row else None

    def list_active_jobs(self) -> list[PipelineJob]:
        """返回全部运行中或等待审核的任务，供 Gateway 重启恢复。"""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM pipeline_jobs
                WHERE status IN (?, ?, ?)
                ORDER BY started_at ASC
                """,
                (STATUS_RUNNING, STATUS_WAIT_HC, STATUS_STOPPING),
            ).fetchall()
        return [self._row_to_job(row) for row in rows]

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

    def update_project_dir(self, job_id: str, project_dir: str) -> None:
        """记录 Coordinator 创建后的真实沙箱项目目录。"""
        with self._lock:
            self._conn.execute(
                "UPDATE pipeline_jobs SET project_dir = ?, updated_at = ? WHERE job_id = ?",
                (project_dir, _now(), job_id),
            )

    def mark_wait_hc(self, job_id: str, hc_kind: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE pipeline_jobs SET status = ?, current_phase = ?, updated_at = ? "
                "WHERE job_id = ?",
                (STATUS_WAIT_HC, hc_kind, _now(), job_id),
            )
        self._notify_status(job_id, STATUS_WAIT_HC)

    def resume_running(self, job_id: str, next_phase: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE pipeline_jobs SET status = ?, current_phase = ?, updated_at = ?, "
                "finished_at = NULL, last_error = '', failure_scope = '', "
                "retry_strategy = '' WHERE job_id = ?",
                (STATUS_RUNNING, next_phase, _now(), job_id),
            )
        self._notify_status(job_id, STATUS_RUNNING)

    def mark_done(self, job_id: str, online_url: str = "") -> None:
        now = _now()
        with self._lock:
            self._conn.execute(
                "UPDATE pipeline_jobs SET status = ?, finished_at = ?, updated_at = ?, "
                "online_url = COALESCE(NULLIF(?, ''), online_url), last_error = '', "
                "failure_scope = '', retry_strategy = '' "
                "WHERE job_id = ?",
                (STATUS_DONE, now, now, online_url, job_id),
            )
        self._notify_status(job_id, STATUS_DONE)

    def mark_failed(
        self,
        job_id: str,
        error: str,
        *,
        failure_scope: str = FAILURE_SCOPE_RECOVERABLE,
        retry_strategy: str = RETRY_STRATEGY_RESUME_SESSION,
    ) -> None:
        if failure_scope not in {
            FAILURE_SCOPE_RECOVERABLE,
            FAILURE_SCOPE_SESSION,
            FAILURE_SCOPE_TASK,
        }:
            raise ValueError(f"invalid failure_scope: {failure_scope}")
        if retry_strategy not in {"", RETRY_STRATEGY_RESUME_SESSION}:
            raise ValueError(f"invalid retry_strategy: {retry_strategy}")
        now = _now()
        with self._lock:
            self._conn.execute(
                "UPDATE pipeline_jobs SET status = ?, finished_at = ?, updated_at = ?, "
                "last_error = ?, failure_scope = ?, retry_strategy = ? WHERE job_id = ?",
                (
                    STATUS_FAILED,
                    now,
                    now,
                    error,
                    failure_scope,
                    retry_strategy,
                    job_id,
                ),
            )
        self._notify_status(job_id, STATUS_FAILED)

    def claim_retry(self, job_id: str) -> bool:
        """Atomically claim one failed/recoverable job for a retry callback."""
        with self._lock:
            cursor = self._conn.execute(
                """
                UPDATE pipeline_jobs
                SET status = ?, finished_at = NULL, updated_at = ?
                WHERE job_id = ? AND status = ? AND failure_scope IN ('', ?)
                """,
                (
                    STATUS_RUNNING,
                    _now(),
                    job_id,
                    STATUS_FAILED,
                    FAILURE_SCOPE_RECOVERABLE,
                ),
            )
        return cursor.rowcount == 1

    def mark_stopping(self, job_id: str, operator_open_id: str) -> bool:
        now = _now()
        with self._lock:
            cursor = self._conn.execute(
                """
                UPDATE pipeline_jobs
                SET status = ?, stop_requested_at = ?, stop_requested_by = ?,
                    updated_at = ?
                WHERE job_id = ? AND status IN (?, ?)
                """,
                (
                    STATUS_STOPPING,
                    now,
                    operator_open_id,
                    now,
                    job_id,
                    STATUS_RUNNING,
                    STATUS_WAIT_HC,
                ),
            )
        if cursor.rowcount:
            self._notify_status(job_id, STATUS_STOPPING)
        return cursor.rowcount == 1

    def mark_stopped(self, job_id: str, reason: str = "user_interrupt") -> None:
        now = _now()
        with self._lock:
            self._conn.execute(
                "UPDATE pipeline_jobs SET status = ?, finished_at = ?, updated_at = ?, "
                "last_error = ? WHERE job_id = ?",
                (STATUS_STOPPED, now, now, reason, job_id),
            )
        self._notify_status(job_id, STATUS_STOPPED)

    def set_progress_card_message_id(self, job_id: str, message_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE pipeline_jobs SET progress_card_message_id = ?, updated_at = ? "
                "WHERE job_id = ?",
                (message_id or "", _now(), job_id),
            )

    def set_topic_thread_id(self, job_id: str, thread_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE pipeline_jobs SET topic_thread_id = ?, updated_at = ? "
                "WHERE job_id = ?",
                (thread_id or "", _now(), job_id),
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
        operator_label: str = "",
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                UPDATE pipeline_hc_events
                SET user_decision = ?, user_note = ?, operator_label = ?, resolved_at = ?
                WHERE id = ?
                """,
                (user_decision, user_note, operator_label, _now(), event_id),
            )

    def get_hc_event(self, event_id: int) -> Optional[HcEvent]:
        """按主键 id 反查一条 HC 记录。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM pipeline_hc_events WHERE id = ?", (event_id,)
            ).fetchone()
        return self._row_to_hc(row) if row else None

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

    def list_resolved_hc_for_job(self, job_id: str) -> list[HcEvent]:
        """按发生顺序返回任务已完成的审核记录，供运行卡持续展示。"""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM pipeline_hc_events
                WHERE job_id = ? AND resolved_at IS NOT NULL
                ORDER BY created_at ASC, id ASC
                """,
                (job_id,),
            ).fetchall()
        return [self._row_to_hc(row) for row in rows]

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
            failure_scope=(
                row["failure_scope"] if "failure_scope" in row.keys() else ""
            ),
            retry_strategy=(
                row["retry_strategy"] if "retry_strategy" in row.keys() else ""
            ),
            online_url=row["online_url"],
            progress_card_message_id=(
                row["progress_card_message_id"]
                if "progress_card_message_id" in row.keys()
                else ""
            ),
            root_message_id=(
                row["root_message_id"] if "root_message_id" in row.keys() else ""
            ),
            topic_thread_id=(
                row["topic_thread_id"] if "topic_thread_id" in row.keys() else ""
            ),
            task_id=row["task_id"] if "task_id" in row.keys() else "",
            stop_requested_at=(
                row["stop_requested_at"]
                if "stop_requested_at" in row.keys()
                else None
            ),
            stop_requested_by=(
                row["stop_requested_by"]
                if "stop_requested_by" in row.keys()
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
            operator_label=(
                row["operator_label"] if "operator_label" in row.keys() else ""
            ),
            created_at=row["created_at"],
            resolved_at=row["resolved_at"],
        )
