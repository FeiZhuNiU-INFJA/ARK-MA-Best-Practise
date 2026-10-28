"""topic5 pipeline 长任务映射库(飞书 ↔ 方舟 MA Session)。

用途:把飞书三元组会话键 ``(chat_id, thread_id, user_open_id)`` 映射到方舟 MA Session,
并记录当前 pipeline 所在 phase、mode、project_dir、HC 卡点历史。用于:

  - HC 卡片回调时,反查 ``ma_session_id`` 以调 ``POST /sessions/{id}/events`` 续跑
  - 会话意外中断后,断点恢复(取 ``current_phase``)
  - 运营侧看板查看长任务状态

与 topic6 的差异:topic5 复盘报告通过**飞书文档**交付(不走妙搭 Miaoda),因此:
  - 没有 Miaoda 用户 OAuth,不需要 ``user_oauth`` 表
  - ``online_url`` 语义是最终交付的飞书 docx URL
  - HC 卡点是 DAG 人工门 G1/G2/N10/G3/G4/HC7(对齐 hc_bridge.NODE_TO_HC)

物理独立于 config_store / memory / conversation 库,单独一个 SQLite:
``data/topic5_pipeline.db``(可用 ``TOPIC5_PIPELINE_DB_PATH`` 覆盖)。

连接风格:``check_same_thread=False`` + WAL + ``chmod 0o600``,JSON 字段用 TEXT 序列化。
纯 stdlib(sqlite3 / json / threading),可在沙箱内独立 import 测试。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

DEFAULT_PIPELINE_DB_PATH = str(
    Path(__file__).resolve().parent / "data" / "topic5_pipeline.db"
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
RETRY_STRATEGY_NODE = "retry_node"

# HC 卡点标识(对齐 skills/pipeline-orchestrator/hc_bridge.NODE_TO_HC)。
HC_KINDS = ("G1", "G2", "N10", "G3", "G4", "HC7", "USER_ESCALATE")


def _new_job_id() -> str:
    return f"job_{uuid.uuid4().hex[:12]}"


def _now() -> int:
    return int(time.time())


def _add_business_days(epoch: int, days: int) -> int:
    value = datetime.fromtimestamp(epoch)
    added = 0
    while added < days:
        value += timedelta(days=1)
        if value.weekday() < 5:
            added += 1
    return int(value.timestamp())


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
    """一次 topic5 pipeline 运行 ⇔ 一个方舟 MA Session。"""

    job_id: str
    chat_id: str
    thread_id: str
    user_open_id: str
    ma_session_id: str
    mode: str  # demo | full
    project_dir: str
    current_phase: str = "intake"  # DAG 节点/人工门名,例如 clean / G1 / aggregate / G3
    status: str = STATUS_RUNNING
    started_at: int = field(default_factory=_now)
    updated_at: int = field(default_factory=_now)
    finished_at: Optional[int] = None
    last_error: str = ""
    online_url: str = ""  # 最终交付的飞书 docx URL
    progress_card_message_id: str = ""  # 进度卡片飞书 message_id;用于 patch 覆写
    # 话题模式:触发任务那条飞书消息的 message_id(话题由头)。首发进度卡按它 reply_in_thread
    # 开启话题,之后所有新发消息(HC 卡/文本回执)都 reply 到这条触发消息,落进同一话题。
    root_message_id: str = ""
    # 话题模式:首发卡片 reply_in_thread 后飞书返回的 thread_id(omt_ 开头);会话键语义的
    # thread_id(上面那个字段)是入站事件带来的、用于定位活跃任务,二者不复用,避免混淆。
    topic_thread_id: str = ""
    # 用户在任务话题内上传的活动明细 Excel。file_id 是 Ark Files API 返回值，
    # mount_path 固定挂到 Session 的 /mnt/session/uploads/ 下，供 n07 clean_campaign 读取。
    source_file_id: str = ""
    source_file_name: str = ""
    source_file_mount_path: str = ""
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
    hc_kind: str  # G1 | G2 | N10 | G3 | G4 | HC7 | USER_ESCALATE
    payload: dict  # Agent 结构化输出(hc / node / prompt / expect 等)
    card_message_id: str = ""  # 飞书卡片 message_id,便于事后 patch
    user_decision: str = ""  # pass | reject | remark
    user_note: str = ""
    operator_label: str = ""
    created_at: int = 0
    resolved_at: Optional[int] = None
    remind_at: Optional[int] = None
    escalate_at: Optional[int] = None
    reminded_at: Optional[int] = None
    escalated_at: Optional[int] = None


# ---- DAO -------------------------------------------------------------------


class PipelineStore:
    """topic5 长任务映射表 DAO。"""

    def __init__(self, db_path: Optional[str] = None):
        path = db_path or os.environ.get("TOPIC5_PIPELINE_DB_PATH") or DEFAULT_PIPELINE_DB_PATH
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
                current_phase TEXT NOT NULL DEFAULT 'intake',
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
                source_file_id TEXT NOT NULL DEFAULT '',
                source_file_name TEXT NOT NULL DEFAULT '',
                source_file_mount_path TEXT NOT NULL DEFAULT '',
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
                remind_at INTEGER,
                escalate_at INTEGER,
                reminded_at INTEGER,
                escalated_at INTEGER,
                FOREIGN KEY (job_id) REFERENCES pipeline_jobs(job_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_hc_events_job
                ON pipeline_hc_events(job_id, hc_kind);

            CREATE INDEX IF NOT EXISTS idx_hc_events_card
                ON pipeline_hc_events(card_message_id);

            CREATE TABLE IF NOT EXISTS pipeline_receipts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id TEXT NOT NULL UNIQUE,
                receipt_type TEXT NOT NULL,
                sender TEXT NOT NULL,
                recipient TEXT NOT NULL,
                job_id TEXT NOT NULL,
                correlation_id TEXT NOT NULL,
                causation_id TEXT NOT NULL DEFAULT '',
                payload TEXT NOT NULL DEFAULT '{}',
                created_at INTEGER NOT NULL,
                FOREIGN KEY (job_id) REFERENCES pipeline_jobs(job_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_pipeline_receipts_job
                ON pipeline_receipts(job_id, receipt_type);
            """
        )
        # 老库补列:后加的列 SQLite 没有 IF NOT EXISTS 语法,逐个检查补齐。
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
        if "source_file_id" not in cols:
            self._conn.execute(
                "ALTER TABLE pipeline_jobs ADD COLUMN source_file_id TEXT NOT NULL DEFAULT ''"
            )
        hc_cols = {
            r[1]
            for r in self._conn.execute(
                "PRAGMA table_info(pipeline_hc_events)"
            ).fetchall()
        }
        for name in ("remind_at", "escalate_at", "reminded_at", "escalated_at"):
            if name not in hc_cols:
                self._conn.execute(
                    f"ALTER TABLE pipeline_hc_events ADD COLUMN {name} INTEGER"
                )
        if "source_file_name" not in cols:
            self._conn.execute(
                "ALTER TABLE pipeline_jobs ADD COLUMN source_file_name TEXT NOT NULL DEFAULT ''"
            )
        if "source_file_mount_path" not in cols:
            self._conn.execute(
                "ALTER TABLE pipeline_jobs ADD COLUMN source_file_mount_path TEXT NOT NULL DEFAULT ''"
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
            root_message_id=root_message_id or "",
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
        """会话键唯一活跃任务;同时识别 Bot 创建后的 topic_thread_id。"""
        thread = thread_id or ""
        with self._lock:
            if thread:
                row = self._conn.execute(
                    """
                    SELECT * FROM pipeline_jobs
                    WHERE chat_id = ? AND user_open_id = ?
                      AND (thread_id = ? OR topic_thread_id = ?)
                      AND status IN (?, ?, ?)
                    ORDER BY started_at DESC LIMIT 1
                    """,
                    (
                        chat_id,
                        user_open_id,
                        thread,
                        thread,
                        STATUS_RUNNING,
                        STATUS_WAIT_HC,
                        STATUS_STOPPING,
                    ),
                ).fetchone()
            else:
                # 已创建 topic_thread_id 的任务只能在对应话题内控制，防止主时间线
                # /new、/continue 或备注误命中任意一个并发任务。
                row = self._conn.execute(
                    """
                    SELECT * FROM pipeline_jobs
                    WHERE chat_id = ? AND user_open_id = ?
                      AND thread_id = '' AND topic_thread_id = ''
                      AND status IN (?, ?, ?)
                    ORDER BY started_at DESC LIMIT 1
                    """,
                    (
                        chat_id,
                        user_open_id,
                        STATUS_RUNNING,
                        STATUS_WAIT_HC,
                        STATUS_STOPPING,
                    ),
                ).fetchone()
        return self._row_to_job(row) if row else None

    def get_active_job_by_topic(
        self, chat_id: str, thread_id: str
    ) -> Optional[PipelineJob]:
        """按话题查询任意发起人的活跃任务；主时间线空 thread 不构成隔离键。"""
        thread = thread_id or ""
        if not thread:
            return None
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM pipeline_jobs
                WHERE chat_id = ?
                  AND (thread_id = ? OR topic_thread_id = ?)
                  AND status IN (?, ?, ?)
                ORDER BY started_at DESC LIMIT 1
                """,
                (
                    chat_id,
                    thread,
                    thread,
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

        running/wait_hc/failed/stopped 都纳入候选,以便把被本地误判为死的 Session 捞回。
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
        """返回整个 Topic5 Gateway 最新的活跃任务。"""
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
        """返回全部运行中或等待审核的任务,供 Gateway 重启恢复。"""
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
        """记录 Coordinator 创建后的真实沙箱项目目录(run_dir)。"""
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
        if retry_strategy not in {
            "",
            RETRY_STRATEGY_RESUME_SESSION,
            RETRY_STRATEGY_NODE,
        }:
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
                    error[:1000],
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
        """首发卡片开启话题后,回写飞书返回的 thread_id(话题模式后续发消息的落点键)。"""
        with self._lock:
            self._conn.execute(
                "UPDATE pipeline_jobs SET topic_thread_id = ?, updated_at = ? "
                "WHERE job_id = ?",
                (thread_id or "", _now(), job_id),
            )

    def set_source_file(
        self, job_id: str, file_id: str, file_name: str, mount_path: str
    ) -> None:
        """记录已上传并挂载到 MA Session 的活动明细 Excel。"""
        with self._lock:
            self._conn.execute(
                """
                UPDATE pipeline_jobs
                SET source_file_id = ?, source_file_name = ?,
                    source_file_mount_path = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (file_id or "", file_name or "", mount_path or "", _now(), job_id),
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
            created_at = _now()
            remind_at = _add_business_days(created_at, 2) if hc_kind == "G4" else None
            escalate_at = _add_business_days(created_at, 3) if hc_kind == "G4" else None
            cur = self._conn.execute(
                """
                INSERT INTO pipeline_hc_events
                    (job_id, hc_kind, payload, card_message_id, created_at,
                     remind_at, escalate_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id, hc_kind, _dumps(payload), card_message_id,
                    created_at, remind_at, escalate_at,
                ),
            )
            return int(cur.lastrowid)

    def resolve_hc_event(
        self,
        event_id: int,
        user_decision: str,
        user_note: str = "",
        operator_label: str = "",
    ) -> bool:
        with self._lock:
            cur = self._conn.execute(
                """
                UPDATE pipeline_hc_events
                SET user_decision = ?, user_note = ?, operator_label = ?, resolved_at = ?
                WHERE id = ? AND resolved_at IS NULL
                """,
                (user_decision, user_note, operator_label, _now(), event_id),
            )
            return cur.rowcount == 1

    def claim_hc_event(
        self,
        event_id: int,
        user_decision: str,
        user_note: str = "",
        operator_label: str = "",
    ) -> bool:
        """Atomically claim the current HC version before any resume side effect."""
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                event = self._conn.execute(
                    "SELECT * FROM pipeline_hc_events WHERE id = ?", (event_id,)
                ).fetchone()
                if event is None or event["resolved_at"] is not None:
                    self._conn.execute("ROLLBACK")
                    return False
                job = self._conn.execute(
                    "SELECT status, current_phase FROM pipeline_jobs WHERE job_id = ?",
                    (event["job_id"],),
                ).fetchone()
                latest = self._conn.execute(
                    """
                    SELECT id FROM pipeline_hc_events
                    WHERE job_id = ? AND hc_kind = ?
                    ORDER BY created_at DESC, id DESC LIMIT 1
                    """,
                    (event["job_id"], event["hc_kind"]),
                ).fetchone()
                if (
                    job is None
                    or job["status"] != STATUS_WAIT_HC
                    or job["current_phase"] != event["hc_kind"]
                    or latest is None
                    or latest["id"] != event_id
                ):
                    self._conn.execute("ROLLBACK")
                    return False
                cur = self._conn.execute(
                    """
                    UPDATE pipeline_hc_events
                    SET user_decision = ?, user_note = ?, operator_label = ?, resolved_at = ?,
                        escalated_at = CASE WHEN ? = 'escalate' THEN ? ELSE escalated_at END
                    WHERE id = ? AND resolved_at IS NULL
                    """,
                    (
                        user_decision, user_note, operator_label, _now(),
                        user_decision, _now(), event_id,
                    ),
                )
                self._conn.execute("COMMIT")
                return cur.rowcount == 1
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

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

    def append_current_hc_remark(self, event_id: int, note: str) -> bool:
        """Record a pending remark only on the current unresolved HC version."""
        with self._lock:
            cur = self._conn.execute(
                """
                UPDATE pipeline_hc_events
                SET user_note = ?
                WHERE id = ? AND resolved_at IS NULL
                  AND id = (
                    SELECT e2.id FROM pipeline_hc_events e2
                    JOIN pipeline_jobs j ON j.job_id = e2.job_id
                    WHERE e2.job_id = pipeline_hc_events.job_id
                      AND e2.hc_kind = pipeline_hc_events.hc_kind
                      AND j.status = ? AND j.current_phase = e2.hc_kind
                    ORDER BY e2.created_at DESC, e2.id DESC LIMIT 1
                  )
                """,
                (note, event_id, STATUS_WAIT_HC),
            )
            return cur.rowcount == 1

    def get_pending_hc_by_card(self, card_message_id: str) -> Optional[HcEvent]:
        """飞书卡片回调回来时,凭 card_message_id 反查未 resolve 的 HC 事件。"""
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM pipeline_hc_events
                WHERE card_message_id = ? AND resolved_at IS NULL
                ORDER BY created_at DESC, id DESC LIMIT 1
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
                ORDER BY created_at DESC, id DESC LIMIT 1
                """,
                (job_id, hc_kind),
            ).fetchone()
        return self._row_to_hc(row) if row else None

    def list_resolved_hc_for_job(self, job_id: str) -> list[HcEvent]:
        """按发生顺序返回任务已完成的审核记录,供运行卡持续展示。"""
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

    def list_due_g4_events(self, now: Optional[int] = None) -> list[HcEvent]:
        now = now or _now()
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM pipeline_hc_events
                WHERE hc_kind = 'G4' AND resolved_at IS NULL
                  AND (
                    (remind_at IS NOT NULL AND remind_at <= ? AND reminded_at IS NULL)
                    OR
                    (escalate_at IS NOT NULL AND escalate_at <= ? AND escalated_at IS NULL)
                  )
                ORDER BY created_at ASC, id ASC
                """,
                (now, now),
            ).fetchall()
        return [self._row_to_hc(row) for row in rows]

    def mark_g4_reminded(self, event_id: int, now: Optional[int] = None) -> bool:
        current = now or _now()
        with self._lock:
            cur = self._conn.execute(
                """
                UPDATE pipeline_hc_events
                SET reminded_at = ?, escalate_at = ?
                WHERE id = ? AND resolved_at IS NULL AND reminded_at IS NULL
                """,
                (current, _add_business_days(current, 1), event_id),
            )
            return cur.rowcount == 1

    def mark_g4_escalated(self, event_id: int, now: Optional[int] = None) -> bool:
        with self._lock:
            cur = self._conn.execute(
                """
                UPDATE pipeline_hc_events SET escalated_at = ?
                WHERE id = ? AND resolved_at IS NULL AND reminded_at IS NOT NULL
                  AND escalated_at IS NULL AND escalate_at <= ?
                """,
                (now or _now(), event_id, now or _now()),
            )
            return cur.rowcount == 1

    def append_receipt(
        self,
        *,
        receipt_type: str,
        sender: str,
        recipient: str,
        job_id: str,
        correlation_id: str,
        causation_id: str = "",
        payload: Optional[dict] = None,
        message_id: str = "",
    ) -> str:
        """Append an idempotent A2A receipt with the common message envelope."""
        message_id = message_id or f"msg_{uuid.uuid4().hex}"
        with self._lock:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO pipeline_receipts
                    (message_id, receipt_type, sender, recipient, job_id,
                     correlation_id, causation_id, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id, receipt_type, sender, recipient, job_id,
                    correlation_id, causation_id, _dumps(payload or {}), _now(),
                ),
            )
        return message_id

    def list_receipts(self, job_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM pipeline_receipts
                WHERE job_id = ? ORDER BY created_at ASC, id ASC
                """,
                (job_id,),
            ).fetchall()
        return [
            {
                **dict(row),
                "payload": _loads(row["payload"], {}),
            }
            for row in rows
        ]

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
            source_file_id=(
                row["source_file_id"] if "source_file_id" in row.keys() else ""
            ),
            source_file_name=(
                row["source_file_name"] if "source_file_name" in row.keys() else ""
            ),
            source_file_mount_path=(
                row["source_file_mount_path"]
                if "source_file_mount_path" in row.keys()
                else ""
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
            remind_at=row["remind_at"] if "remind_at" in row.keys() else None,
            escalate_at=(
                row["escalate_at"] if "escalate_at" in row.keys() else None
            ),
            reminded_at=(
                row["reminded_at"] if "reminded_at" in row.keys() else None
            ),
            escalated_at=(
                row["escalated_at"] if "escalated_at" in row.keys() else None
            ),
        )
